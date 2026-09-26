# v1.1.13 Grammar Cap, Idempotent Completion and Review Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Cap every grade-schema `maxLength` at llama.cpp's stock grammar ceiling (1999). Replace `kill_worker`'s status guard with idempotent completion, apply the remaining review fixes, and correct the docs.

**Requirements:** docs/plans/2026-09-26-v1-1-13-grammar-cap-and-idempotent-completion-requirements.md

**Design:** docs/plans/2026-09-26-v1-1-13-grammar-cap-and-idempotent-completion-design.md

**Architecture:**
- llama.cpp hardcodes `MAX_REPETITION_THRESHOLD 2000`, and a json_schema `maxLength` compiles to `char{0,N}`. Measured live, 1999 passes and 2000 fails.
- `grader.py` owns three constants: `LLAMA_CPP_MAX_REPETITION_THRESHOLD = 2000`, the measured `GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1999`, and `GRAMMAR_MAX_STRING_LENGTH = threshold - 1`. Every long schema field already references the cap in the working tree, and guard tests pin it.
- Completion becomes idempotent at the data layer: no re-stamped `finished_at`, and no duplicate `worker_finished`. The seam always runs.
- The seam still owns all completion, and the daemon never calls `update_worker_status`.

**Tech stack:** Python 3.11, pytest, and the live OpenAI-compatible llama.cpp endpoint from `~/.claude/ironclaude-hooks-config.json`.

## Starting state (working tree, verified in Task 2 Step 0)

- `commander/src/ironclaude/{main.py, orchestrator_mcp.py, grader.py, shadow_grader.py}` hold HEAD (`60509a1`) content, plus the grammar-cap wiring:
  - `main.py` and `orchestrator_mcp.py` import `GRAMMAR_MAX_STRING_LENGTH`, and so does `shadow_grader.py`.
  - The seven long fields use it: `main.py` has 3, `orchestrator_mcp.py` has 3 and `shadow_grader.py` has 1.
  - `grader.py` defines it as 1920, beside `GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1984`.
- `worker/hooks/plan-validator.sh` matches HEAD.
- `commander/tests/test_grade_schema_bounds.py` has these edits:
  - it imports `GRAMMAR_MAXLENGTH_HIGHEST_PASS` and `GRAMMAR_MAX_STRING_LENGTH`;
  - `test_prompt_waiting_interaction_block_is_grammar_capped`;
  - the `_max_lengths` helper;
  - `test_grammar_cap_pinned_to_measured_highest_pass`;
  - `test_every_max_length_within_grammar_cap`.
- `commander/docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md` is staged. It covers the 64-step bisect: 1984 passed, 2048 failed.
- Live evidence logs, all in the scratchpad `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/`:
  - `bisect-maxlength.log`: the 64-step bisect;
  - `boundary-2000.log`: the N=2000 probe;
  - `pin-max.log`: the exact binary search from 1984 to 2000.
- Commander is running this working tree (restarted 2026-09-26 20:09) with the cap at 1920.

## Execution invariants (every command below satisfies these)

- **Shell state:** Shell state does not persist between steps. Every command uses absolute paths or its own `cd` prefix.
- **Working directory:** Bash's cwd is `commander/`. Repository-root operations use `git -C /Users/roberthyatt/Code/ironclaude`.
- **Globs:** zsh has `nomatch` set, so globs are quoted.
- **Tests:** Every pytest run uses `PYTHONUNBUFFERED=1`.
- **Git:** There are no commits and no `git stash`, because the index holds staged plan docs.
- **Scratchpad:** The scratchpad logs are evidence. They are read, never modified or deleted.
- **Anchors:** Edits are anchored by content, not line numbers.

---

## Task 1: Findings note — exact ceiling, upstream cause, final constants

No tests required: documentation only.

**Files:**
- Modify: `commander/docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md`

**Step 1: correct the prompt-detection section.** In the section `## Prompt-detection consequence`:
- replace `at most \`GRAMMAR_MAX_STRING_LENGTH\` (1920) characters` with `at most \`GRAMMAR_MAX_STRING_LENGTH\` (1999) characters`;
- replace `longer than 1920 characters` with `longer than 1999 characters`;
- replace `The validator's prefix checks then reject it` with `The validator's suffix check (\`_suffix_is_prompt_chrome\`, \`tmux_manager.py:223\`) then rejects it, because the text after a cut-off block is the rest of that block, not prompt chrome`.

**Step 2: mark the margin rule as superseded.** In `## Method`, replace the sentence ``The cap is `max(1024, HIGHEST_PASS - 64)`.`` with ``That bisect's cap rule was `max(1024, HIGHEST_PASS - 64)` (superseded; see Exact ceiling).``. In `## Results`:
- replace the whole bullet that starts `- **Why the 64 margin matters:**` with: `- **Superseded:** the one-step margin rule (cap 1920) was replaced by the exact search and operator decision below.`
- replace the whole bullet that starts `- **Unprobed range:**` with: `- **Unprobed range:** N between 1985 and 2047 was not probed in this bisect; the exact search below covers 1985–2000.`

**Step 3: add the exact-ceiling section.** Insert this section directly before `## Prompt-detection consequence`:

```markdown
## Exact ceiling (follow-up)

- **N = 2000:** 14 requests (all five schemas; the shadow schema's runs 0–1), every one HTTP 500, on both models. The probe was stopped after 14 because the result was conclusive. Log: `scratchpad/boundary-2000.log`.
- **Binary search between 1984 (pass) and 2000 (fail):** each N stops at its first non-200 response. 1992, 1996, 1998 and 1999 each returned 200 on all 15 requests. Result: `EXACT_HIGHEST_PASS=1999 EXACT_FIRST_FAIL=2000`. Log: `scratchpad/pin-max.log`.
- **Upstream cause (reported by the amd-halo box):** llama.cpp hardcodes `MAX_REPETITION_THRESHOLD 2000` in `llama-grammar.cpp`. It is the stock default, unchanged on current master, with no runtime flag. A json_schema `maxLength` compiles to a `char{0,N}` rule, so every stock llama.cpp deployment has this limit. The measured rule is exactly N < 2000.
- **Cap:** the operator chose `GRAMMAR_MAX_STRING_LENGTH = LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1` = 1999, with no extra margin.
```

**Step 4: replace the closing constants.** Replace the final three lines of the file (`GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1984`, `GRAMMAR_MAXLENGTH_FIRST_FAIL = 2048`, `GRAMMAR_MAX_STRING_LENGTH = 1920`) with:

```
LLAMA_CPP_MAX_REPETITION_THRESHOLD = 2000
GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1999
GRAMMAR_MAXLENGTH_FIRST_FAIL = 2000
GRAMMAR_MAX_STRING_LENGTH = 1999
```

**Step 5: verify.**

```bash
rg -n -e "^LLAMA_CPP_MAX_REPETITION_THRESHOLD = " -e "^GRAMMAR_" /Users/roberthyatt/Code/ironclaude/commander/docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md
```

Expected: exactly four lines, with the values 2000, 1999, 2000 and 1999 in the order above.

```bash
rg -n -F "prefix checks" /Users/roberthyatt/Code/ironclaude/commander/docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md
```

Expected: no output.

```bash
rg -n -F "EXACT_HIGHEST_PASS=1999 EXACT_FIRST_FAIL=2000" /Users/roberthyatt/Code/ironclaude/commander/docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md
```

Expected: one match.

**Step 6: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md
```

---

## Task 2: Set the grammar cap to the stock llama.cpp ceiling (1999)

Depends on Task 1.

**Files:**
- Modify: `commander/src/ironclaude/grader.py`
- Test: `commander/tests/test_grade_schema_bounds.py`
- Stage (already wired, not edited): `commander/src/ironclaude/main.py`, `commander/src/ironclaude/orchestrator_mcp.py`, `commander/src/ironclaude/shadow_grader.py`

**Step 0: verify the starting state.**

```bash
git -C /Users/roberthyatt/Code/ironclaude status --short -- worker/hooks/plan-validator.sh
```

Expected: no output, because the hook matches HEAD.

```bash
rg -n -F '"maxLength": GRAMMAR_MAX_STRING_LENGTH' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude
```

Expected: seven matches. `main.py` has 3 (`interaction_block`, `question`, `authority_text`), `orchestrator_mcp.py` has 3 (`feedback`, `feedback`, `diagnosis`) and `shadow_grader.py` has 1 (`feedback`).

```bash
rg -n -e '"maxLength": 2048' -e '"maxLength": 4096' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude
```

Expected: no output.

**Step 1 (RED).** In `test_grade_schema_bounds.py`:

(a) Replace the import line

```python
from ironclaude.grader import GRAMMAR_MAXLENGTH_HIGHEST_PASS, GRAMMAR_MAX_STRING_LENGTH
```

with:

```python
from ironclaude.grader import (
    GRAMMAR_MAXLENGTH_HIGHEST_PASS,
    GRAMMAR_MAX_STRING_LENGTH,
    LLAMA_CPP_MAX_REPETITION_THRESHOLD,
)
```

(b) Replace the whole function `test_grammar_cap_pinned_to_measured_highest_pass`, including its comment lines, with:

```python
def test_grammar_cap_pinned_to_stock_llama_cpp_ceiling():
    # llama.cpp hardcodes MAX_REPETITION_THRESHOLD 2000 (llama-grammar.cpp) and
    # maxLength compiles to char{0,N}; measured live, 1999 passes and 2000 fails
    # (docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md). A bump to
    # 2000 or beyond breaks every grade call with HTTP 500.
    assert LLAMA_CPP_MAX_REPETITION_THRESHOLD == 2000
    assert GRAMMAR_MAXLENGTH_HIGHEST_PASS == 1999
    assert GRAMMAR_MAX_STRING_LENGTH == GRAMMAR_MAXLENGTH_HIGHEST_PASS
    assert GRAMMAR_MAX_STRING_LENGTH == LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1
```

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_grade_schema_bounds.py
```

Expected (predicted): ERROR at collection, because `LLAMA_CPP_MAX_REPETITION_THRESHOLD` cannot be imported from `ironclaude.grader`.

**Step 2 (GREEN).** In `grader.py`, replace the block that starts with the comment line `# Highest per-string maxLength every real grade schema passed` and ends with `GRAMMAR_MAX_STRING_LENGTH = 1920` (eight comment and assignment lines, `grader.py:37-44`) with:

```python
# llama.cpp hardcodes MAX_REPETITION_THRESHOLD 2000 in llama-grammar.cpp (stock
# default, unchanged on master, no runtime flag). A json_schema maxLength compiles
# to a char{0,N} rule, so N >= 2000 fails to build the grammar: HTTP 500 "Failed
# to parse input" and unconstrained output. Measured live on the deployed server
# (grader and shadow models, every real grade schema): 1999 passes, 2000 fails.
# See docs/plans/2026-09-26-grammar-maxlength-bisect-findings.md.
LLAMA_CPP_MAX_REPETITION_THRESHOLD = 2000
GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1999
# The cap every long schema field uses: the stock ceiling itself.
GRAMMAR_MAX_STRING_LENGTH = LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1
```

Run the Step 1 command again. Expected: 0 failed.

**Step 3: verify the constants.**

```bash
rg -n -e "^LLAMA_CPP_MAX_REPETITION_THRESHOLD = " -e "^GRAMMAR_MAX" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/grader.py
```

Expected: exactly three lines: `LLAMA_CPP_MAX_REPETITION_THRESHOLD = 2000`, `GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1999` and `GRAMMAR_MAX_STRING_LENGTH = LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1`.

```bash
rg -n -e '"maxLength": 2048' -e '"maxLength": 4096' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude
```

Expected: no output.

**Step 4: regression.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_grade_schema_bounds.py tests/test_local_grader.py tests/test_shadow_grader.py tests/test_orchestrator_mcp.py tests/test_daemon.py
```

Use a Bash timeout of 600000. Expected: 0 failed.

**Step 5: live release-gate probe at the new cap.** This sends the five real schemas, now at 1999, through `LocalGrader`, plus the shadow schema through the shadow spot model.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -c "
import json, requests
import ironclaude.main as m
from ironclaude.grader import LocalGrader, GRAMMAR_MAX_STRING_LENGTH
from ironclaude.orchestrator_mcp import OrchestratorTools as O
from ironclaude.shadow_grader import GRADER_VERDICT_SCHEMA as SH
from ironclaude.backend_resolver import resolve_backend
print('cap', GRAMMAR_MAX_STRING_LENGTH, m._PROMPT_WAITING_SCHEMA['properties']['interaction_block']['maxLength'])
g = LocalGrader()
block = 'Effort 1 = Stage-7 boundary snap now (cheap Stage-7-only re-run + re-verify). ' * 20
tail = ('step output line ok ' * 200) + chr(10) + block + chr(10) + 'Which effort should I run first? (1) boundary snap (2) full rerun'
cases = [('prompt_waiting', m._PROMPT_WAITING_SYSTEM, 'Worker terminal context:' + chr(10) + tail[-m.PROMPT_CAPTURE_CHARS:], m._PROMPT_WAITING_SCHEMA),
         ('verdict', 'You grade decisions.', 'Grade: worker finished, tests pass. Detailed feedback.', O._LOCAL_VERDICT_SCHEMA),
         ('confidence', 'You grade objectives.', 'Grade objective: implement X in src/foo.py with tests.', O._LOCAL_CONFIDENCE_SCHEMA),
         ('health', 'You diagnose worker health.', 'Terminal output:' + chr(10) + 'running tests... ok' * 30, O._LOCAL_HEALTH_SCHEMA),
         ('shadow', 'You grade decisions.', 'Grade: worker finished; verdict with feedback.', SH)]
for name, sysp, user, sc in cases:
    r = g.grade(sysp, user, sc)
    print(name, 'INFRA_ERROR ' + str(r.get('error_detail')) if r.get('infrastructure_error') else 'OK')
cfg = json.load(open('/Users/roberthyatt/.claude/ironclaude-hooks-config.json'))
sh = resolve_backend(cfg, 'shadow')
r = requests.post(sh.url.rstrip('/') + '/chat/completions', json={'model': sh.model, 'messages': [{'role': 'user', 'content': 'Grade: worker finished; verdict with feedback.'}], 'max_tokens': 8192, 'temperature': 0.1, 'reasoning_effort': 'none', 'chat_template_kwargs': {'enable_thinking': False}, 'response_format': {'type': 'json_schema', 'json_schema': {'name': 'verdict', 'schema': SH}}}, timeout=300)
print('shadow-model', sh.model, 'OK' if r.status_code == 200 else 'INFRA_ERROR HTTP ' + str(r.status_code))
"
```

Use a Bash timeout of 600000. Expected: the first line is `cap 1999 1999`, followed by six lines that each end `OK`. Any `INFRA_ERROR` fails the gate.

**Step 6: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/grader.py commander/src/ironclaude/main.py commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/shadow_grader.py commander/tests/test_grade_schema_bounds.py
```

---

## Task 3: Idempotent completion replaces the status guard; `WORKER_KILLED` on the no-row path; seam action in the status

Depends on Task 2, because both tasks touch `orchestrator_mcp.py`.

**Files:**
- Modify: `commander/src/ironclaude/worker_registry.py` (`update_worker_status`)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (`kill_worker`)
- Test: `commander/tests/test_worker_registry.py`, `commander/tests/test_worker_finalize_release.py`, `commander/tests/test_orchestrator_mcp.py`

**Step 1 (RED).**

(a) In `test_worker_registry.py`, append these to class `TestWorkers`:

```python
    def test_completing_already_completed_worker_keeps_finished_at(self, registry):
        registry.register_worker("worker-1", "claude-max", "worker-1")
        registry.update_worker_status("worker-1", "completed")
        registry._conn.execute(
            "UPDATE workers SET finished_at = '2000-01-01 00:00:00' WHERE id = 'worker-1'"
        )
        registry._conn.commit()
        registry.update_worker_status("worker-1", "completed")
        assert registry.get_worker("worker-1")["finished_at"] == "2000-01-01 00:00:00"

    def test_completing_running_worker_sets_finished_at(self, registry):
        registry.register_worker("worker-1", "claude-max", "worker-1")
        assert registry.get_worker("worker-1")["finished_at"] is None
        registry.update_worker_status("worker-1", "completed")
        assert registry.get_worker("worker-1")["finished_at"] is not None
```

(b) In `test_worker_finalize_release.py`, delete the classes `TestKillWorkerAlreadyTerminal` and `TestKillWorkerGuardLivenessFailure` entirely, and append:

```python
class TestKillWorkerIdempotentCompletion:
    """kill_worker has no registry-status guard: the seam always runs (a
    commit_worker-recycled worker is 'completed' yet may hold new reviewed work).
    Duplicate completion is prevented by idempotency: no second worker_finished,
    and the registry does not re-stamp finished_at."""

    def test_already_completed_worker_runs_seam_without_duplicate_finished(self):
        tools = _unmanaged_kill_tools(has_session=False, status="completed")
        seam = MagicMock(return_value={"action": "completed"})
        tools._finalize_and_release_worker = seam

        result = tools.kill_worker("w9")

        seam.assert_called_once()
        finished = [
            c for c in tools.registry.log_event.call_args_list
            if c.args and c.args[0] == "worker_finished"
        ]
        assert finished == []
        assert result["status"] == (
            "Worker w9 killed; already completed (not re-recorded); "
            "seam result: completed."
        )

    def test_recycled_completed_worker_reaches_seam_integrate(self):
        tools = _unmanaged_kill_tools(has_session=True, status="completed")
        seam = MagicMock(return_value={"state": "cleaned", "integratedCommit": "a" * 40})
        tools._finalize_and_release_worker = seam

        result = tools.kill_worker("w9")

        seam.assert_called_once()
        assert result["status"] == (
            "Worker w9 killed; already completed (not re-recorded); "
            "seam result: cleaned."
        )

    def test_already_completed_worker_seam_failure_does_not_claim_uncompleted(self):
        tools = _unmanaged_kill_tools(has_session=False, status="completed")
        tools._finalize_and_release_worker = MagicMock(
            return_value={"failure_phase": "finalization", "error": "boom"}
        )
        result = tools.kill_worker("w9")
        assert "worker remains completed" in result["status"]
        assert "NOT completed" not in result["status"]
        assert not [c for c in tools.registry.log_event.call_args_list
                    if c.args and c.args[0] == "worker_finished"]

    def test_failed_dead_session_worker_still_runs_seam(self):
        tools = _unmanaged_kill_tools(has_session=False, status="failed")

        result = tools.kill_worker("w9")

        tools.registry.update_worker_status.assert_called_once_with("w9", "completed")
        assert result["status"] == "Worker w9 killed and marked completed."


class TestKillWorkerSeamActionInStatus:
    def test_action_only_seam_result_reported(self):
        tools = _unmanaged_kill_tools(has_session=False, status="running")
        tools._finalize_and_release_worker = MagicMock(return_value={"action": "surfaced"})

        result = tools.kill_worker("w9")

        assert "state=surfaced" in result["status"]
        assert "state=unknown" not in result["status"]
```

(c) In `test_orchestrator_mcp.py`, append this to class `TestKillWorker`. It uses the same caplog/JSON pattern as `test_kill_worker_logs_pane_pid`:

```python
    def test_kill_worker_no_registry_row_emits_worker_killed(self, tools, mock_tmux, caplog):
        with caplog.at_level(logging.INFO, logger="ironclaude.orchestrator_mcp"):
            tools.kill_worker("nonexistent")
        killed = []
        for r in caplog.records:
            try:
                data = json.loads(r.getMessage())
                if data.get("event_type") == "WORKER_KILLED":
                    killed.append(data)
            except (json.JSONDecodeError, TypeError):
                pass
        assert len(killed) == 1
        assert killed[0]["worker_id"] == "nonexistent"
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_registry.py tests/test_worker_finalize_release.py tests/test_orchestrator_mcp.py -k "finished_at or IdempotentCompletion or SeamActionInStatus or no_registry_row_emits or seam_failure_does_not_claim"
```

Expected (predicted, not yet measured):
- Fail:
  - `test_completing_already_completed_worker_keeps_finished_at`: the registry re-stamps `finished_at` today.
  - The three completed-worker `TestKillWorkerIdempotentCompletion` tests: the guard skips the seam today.
  - `test_action_only_seam_result_reported`: today the status reads `state=unknown`.
  - `test_kill_worker_no_registry_row_emits_worker_killed`: no event is emitted today.
- Pass:
  - `test_completing_running_worker_sets_finished_at`.
  - `test_failed_dead_session_worker_still_runs_seam`: a regression guard.

Record the measured result line.

**Step 2 (GREEN).**

(a) In `worker_registry.py` `update_worker_status`, replace the whole method body with:

```python
        # Idempotent terminal stamp: finished_at is set only on a real status
        # change, so re-completing an already-completed worker is a no-op for
        # finished_at (no re-stamp on a repeated kill/finalize).
        # Consequence: a commit_worker-recycled worker's leaked-worktree TTL
        # (main._find_leaked_worktrees) runs from its FIRST completion; the
        # reaper's liveness gate still protects a live session.
        if status in ("completed", "failed", "killed"):
            self._conn.execute(
                "UPDATE workers SET finished_at = datetime('now') "
                "WHERE id = ? AND status IS NOT ?",
                (worker_id, status),
            )
        self._conn.execute(
            "UPDATE workers SET status = ? WHERE id = ?", (status, worker_id)
        )
        self._conn.commit()
```

(b) In `orchestrator_mcp.py` `kill_worker`:

- **Remove the guard.** Delete the comment block that starts `# An already-terminal worker has nothing left to grade, finalize or`, and the entire `if _prior_status in ("completed", "killed"):` block, including its `try/except` and `return`. Keep the line `_prior_status = _kw.get("status")`, and add this comment directly above it:

```python
        # No registry-status guard: a 'completed' worker may be a commit_worker-
        # recycled worker still holding reviewed work, so the seam always runs.
        # Duplicate completion is prevented by idempotency instead (registry
        # does not re-stamp finished_at; worker_finished only on a real change).
```

- **Log the no-row kill.** In the no-registry-row branch (`if _kw is None:`), directly after `self.tmux.kill_session(session_name, ssh_host=ssh_host)`, add:

```python
            log_worker_event(
                "WORKER_KILLED",
                worker_id=worker_id,
                pane_pid=None,
                had_evidence=bool(original_objective and evidence),
                kill_reason=evidence[:200] if evidence else None,
                runtime_seconds=None,
            )
```

- **Log `worker_finished` only on a real completion.** Replace:

```python
        if _completed:
            self.registry.log_event("worker_finished", worker_id=worker_id)
```

with:

```python
        _already_completed = _prior_status == "completed"
        if _completed and not _already_completed:
            self.registry.log_event("worker_finished", worker_id=worker_id)
```

- **Report an already-completed worker.** In the status block, replace:

```python
        if _completed:
            _status = f"Worker {worker_id} killed and marked completed."
```

with:

```python
        _seam_result = (
            (_release.get("state") or _release.get("action") or "unknown")
            if isinstance(_release, dict) else "unknown"
        )
        if _completed and _already_completed:
            _status = (
                f"Worker {worker_id} killed; already completed (not re-recorded); "
                f"seam result: {_seam_result}."
            )
        elif _completed:
            _status = f"Worker {worker_id} killed and marked completed."
```

- **Use the seam result in the `_seam_ok` branch.** In the `elif _seam_ok:` branch, replace `f"state={_release.get('state') or 'unknown'} but did not complete "` with `f"state={_seam_result} but did not complete "`.

- **Replace the final failure branch.** Replace the final `else:` status block (the `finalization FAILED` branch) with:

```python
        else:
            _phase = _release.get("failure_phase") if isinstance(_release, dict) else None
            _error = _release.get("error") if isinstance(_release, dict) else None
            _outcome = (
                "worker remains completed (it was completed before this call)"
                if _already_completed else "worker NOT completed"
            )
            _status = (
                f"Worker {worker_id} session killed, but finalization FAILED "
                f"(phase={_phase or 'unknown'}: {_error or 'no result'}) — {_outcome}; "
                f"work preserved; {_retry_clause}"
            )
```

Run the Step 1 command again. Expected: 0 failed.

**Step 3: confirm the guard is gone.**

```bash
rg -n -F 'if _prior_status in ("completed", "killed"):' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py
```

Expected: no output.

```bash
rg -n -F "session state unknown" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py
```

Expected: no output.

**Step 4: regression over every caller.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_registry.py tests/test_worker_finalize_release.py tests/test_orchestrator_mcp.py tests/test_idle_enforcement.py tests/test_kill_worker_log_cap.py tests/test_daemon.py
```

Use a Bash timeout of 600000. Expected: 0 failed. The existing `TestKillWorkerFailureStatusWording` and `TestKillWorkerRetryClause` substring and endswith assertions still hold: for a non-completed prior status, `_outcome` is "worker NOT completed".

**Step 5: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_registry.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_registry.py commander/tests/test_worker_finalize_release.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 4: A terminal `None` outcome is neutral for the consecutive count

Depends on Tasks 2 and 3. It touches `main.py` after Task 2, and Task 3's regression runs `tests/test_daemon.py`, which this task edits.

**Files:**
- Modify: `commander/src/ironclaude/main.py` (`_drive_finalization_recovery`)
- Test: `commander/tests/test_daemon.py` (`TestTerminalFinalizeFailureSurface`)

**Step 1 (RED).** In `test_daemon.py`:
- In `test_non_counted_terminal_outcome_resets_consecutive_count`, remove `None,` from the `@pytest.mark.parametrize("interleaved", [...])` list. Keep `{"state": "integrated"}` and the drift case.
- Append this to `TestTerminalFinalizeFailureSurface`:

```python
    def test_terminal_none_outcome_is_neutral(self, daemon):
        # None = orchestrator unavailable or seam raised: unknown, not success.
        for _ in range(2):
            daemon._drive_finalization_recovery("w1", self._OUTCOME, terminal=True)
        daemon._drive_finalization_recovery("w1", None, terminal=True)
        assert daemon._finalize_failure_count["w1"] == 2
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py -k TestTerminalFinalizeFailureSurface
```

Expected (predicted): `test_terminal_none_outcome_is_neutral` fails, because today the count is popped (a `KeyError` or a count mismatch). All other tests pass.

**Step 2 (GREEN).** In `main.py` `_drive_finalization_recovery`:
- Replace `if terminal and not counted_failure:` with `if terminal and outcome is not None and not counted_failure:`.
- Directly below the existing comment line `# a counted (non-finalization) failure breaks the streak.`, add the comment line `# A None outcome (handle unavailable / seam raised) is unknown: neutral.`
- In the docstring, the sentence wraps across two lines:

```
                         its own _finalize_failure_alerted gate; any other
                         terminal outcome resets the count.
```

Replace those two lines with:

```
                         its own _finalize_failure_alerted gate; any other
                         known terminal outcome resets the count; a None
                         outcome (handle unavailable / seam raised) is neutral.
```

Run the Step 1 command again. Expected: 0 failed.

```bash
rg -n -F "terminal outcome resets the count." /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/main.py
```

Expected: no output. Before the edit this matches the docstring line `terminal outcome resets the count.`

```bash
rg -n -F "known terminal outcome resets the count; a None" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/main.py
```

Expected: one match.

**Step 3: regression.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py
```

Use a Bash timeout of 600000. Expected: 0 failed.

**Step 4: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

---

## Task 5: Documentation corrections and the full suite

Depends on Tasks 2, 3 and 4. No tests required: this task changes documentation only, and the full pytest run is the gate.

**Files:**
- Modify: `CHANGELOG.md` (`## 1.1.13`), `README.md` (`## What's New in v1.1.13`)

**Step 1: CHANGELOG `kill_worker` bullet.** Rewrite the bullet that starts ``- **`kill_worker` no longer re-completes an already-terminal worker`` so that it:
- (i) describes idempotent completion: `update_worker_status` no longer re-stamps `finished_at` for an unchanged status, and `kill_worker` logs `worker_finished` only on a real completion;
- (ii) says the seam always runs, so a `commit_worker`-recycled worker's new reviewed work still integrates;
- (iii) keeps the no-registry-row sentence, and adds that it now emits the `WORKER_KILLED` audit event;
- (iv) keeps the honest-completion sentence, with the `— the daemon will retry —` aside removed;
- (v) keeps the retry-claim sentence ("daemon will retry" only if the status is still `running`), and adds that an already-completed worker whose seam fails is reported as remaining completed;
- (vi) drops the "session state unknown" sentence;
- (vii) ends with `` (`orchestrator_mcp.py`, `worker_registry.py`) ``;
- (viii) adds: "Because `finished_at` is no longer re-stamped, a `commit_worker`-recycled worker's leaked-worktree TTL runs from its first completion, not its latest commit; a live session is still protected by the reaper's liveness gate and release preserves work."

**Step 2: CHANGELOG grammar bullets.**
- In the bullet that starts `- **Every schema passed to the local grader now bounds its output length`, replace the prompt-waiting bounds sentence with: "Every long field is capped at the shared `GRAMMAR_MAX_STRING_LENGTH` (1999 characters). llama.cpp hardcodes `MAX_REPETITION_THRESHOLD` 2000 in its json_schema grammar builder, so a `maxLength` of 2000 or more fails every grade call with HTTP 500. This was measured live: 1999 passes, 2000 fails."
- In the same bullet, change `can no longer be truncated at the token cap into non-JSON` to `is no longer cut off at the token cap into non-JSON in practice`.
- Append to the same bullet: "Consequence: a final interaction block (or question) longer than `GRAMMAR_MAX_STRING_LENGTH` is truncated by the grammar and rejected by the prompt validator, so it is not detected as a prompt; the validator's own ceiling remains 4096."

**Step 3: README.**
- In the bullet that starts `- **The Commander's local grader can no longer return truncated JSON.**`, change the title to `**The Commander's local grader no longer returns truncated JSON in practice.**`.
- In the same bullet, change `caps its output length in the sampling grammar` to `caps each text field at 1999 characters (llama.cpp's grammar ceiling) in the sampling grammar`.
- Add one plain sentence to that bullet: a worker prompt whose final block is longer than that length is not detected.
- In the bullet that starts ``- **`kill_worker` no longer re-completes an already-finished worker.**``, replace its sentences with plain-language equivalents of Step 1 items (i), (ii) and (v).

**Step 3b: consecutive-reset wording (Task 4).**
- In the CHANGELOG bullet that starts `- **The terminal-finalize-failure surface now has its own once-per-worker alert gate`, change `on any terminal outcome that is not a counted (non-\`finalization\`-phase) failure` to `on any known terminal outcome that is not a counted (non-\`finalization\`-phase) failure (a \`None\` outcome — handle unavailable or seam raised — is neutral)`.
- In README line 20, change `now resets on any other terminal outcome instead of climbing forever` to `now resets on any other known terminal outcome instead of climbing forever`.

**Step 4: verify.**

```bash
rg -n -F "resets on any other known terminal outcome" /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: one match.

```bash
rg -n -F "session state unknown" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: no output.

```bash
rg -n -F "— the daemon will retry —" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: no output.

```bash
rg -n -F "grader can no longer return truncated JSON" /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: no output. This phrase appeared on README line 22 before the change; the unrelated "can no longer" phrases on lines 20 and 191 are intentionally left as they are.

```bash
rg -n -F "MAX_REPETITION_THRESHOLD" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: at least one match, all in the `## 1.1.13` grammar bullet.

```bash
rg -n -F "is truncated by the grammar and rejected by the prompt validator" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md
```

Expected: one match.

**Step 5: full pytest.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Use a Bash timeout of 600000. Expected: 0 failed. Record the exact pass line.

**Step 6: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md README.md
```

---

## After the loop (operator-gated, outside professional mode)

1. Amend `60509a1` with the full staged set, with no trailers, and move the local `v1.1.13` tag.
2. Redeploy: copy `worker/hooks/plan-validator.sh` into the Codex 1.1.13 cache, then restart Commander.
3. Re-validate live: rerun the Task 2 Step 5 probe against the running deploy, and watch the daemon log for grader `Non-JSON` or HTTP 500 errors.
4. Push only on an explicit go.
