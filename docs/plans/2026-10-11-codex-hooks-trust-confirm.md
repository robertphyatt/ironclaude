# Codex Hooks-Trust Confirm (R7b) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Codex's "Hooks need review" prompt is confirmed, not just highlighted, by both the Brain trust warm-up and worker spawn. The Codex Brain then starts with its restriction hooks trusted, and the parity live acceptance passes.

**Requirements:** `docs/plans/2026-10-10-codex-brain-parity-design.md` (R7b, plus the second live start-up finding). The operator chose "A: Retreat + fix" and "Include it" on 2026-10-11.

**Architecture:** In Codex v0.162.0-alpha.17.2, the key `2` only moves the highlight to "Trust all and continue", and `Enter` confirms it. Both callers send `["2", "Enter"]` in one `send_raw_keys` call. The warm-up helper reports success only after the prompt leaves the screen. The canary stays the proof, and the live acceptance re-runs.

**Tech Stack:** Python 3 (Commander), pytest, tmux, Codex CLI 0.162.0-alpha.17.2.

**Loop:** full. This touches the guard/trust path of the Brain's restriction hooks and worker spawn.

## Execution invariants

- Shell state does not persist between steps. Use absolute paths.
- Bash cwd is `commander/`. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- zsh `nomatch`: quote globs.
- No foreground `sleep`.
- `docs/` is gitignored, so stage docs with `git add -f`.
- Run pytest with `PYTHONUNBUFFERED=1`.
- Kill processes only by exact PID or exact session name.
- Guard false positive: build any "git push" text with `printf 'git p\165sh …'`, and grep `pu[s]h`.
- The Commander relaunch is done by the operator from their terminal: `make -C ~/Code/ironclaude/commander run`. A safety filter blocks the agent-side relaunch command, so it is not re-sent.

---

## Task 1: Confirm the hooks-review selection in both callers (R7b)

**Files:**
- Modify: `commander/src/ironclaude/main.py:5847-5869` (`_answer_codex_hooks_review`)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:2326-2337` (docstring) and `:2378-2380` (`send_raw_keys`)
- Test: `commander/tests/test_codex_hook_trust_warmup.py`
- Test: `commander/tests/test_worker_adapter.py:158`, `:284`

**Step 1 (RED): update the tests.**

In `commander/tests/test_codex_hook_trust_warmup.py`:
- Line 54 becomes `tmux.send_raw_keys.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, ["2", "Enter"])`.
- In `test_prompt_that_never_clears_is_pressed_only_once`, `assert answered is True` becomes `assert answered is False`. The test keeps `assert tmux.send_raw_keys.call_count == 1`.
- Append:

```python
def test_warmup_reports_unanswered_when_prompt_stays(tmp_path, caplog):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = _warmup_tools(PROMPT)  # never clears: the selection was not confirmed
    with caplog.at_level("INFO"):
        _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5,
                                  settle_seconds=2, clear_seconds=1)
    assert "trust_prompt_answered=False" in caplog.text
    assert "still on screen after answering" in caplog.text
    tmux.send_raw_keys.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, ["2", "Enter"])
```

The warm-up uses real `time.sleep`, so this test passes `clear_seconds=1` to bound its run time to about 1 s.

In `commander/tests/test_worker_adapter.py`, lines 158 and 284 become `assert tmux.raw_sent == [["2", "Enter"]]`.

**Step 2: run RED.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_hook_trust_warmup.py tests/test_worker_adapter.py -q
```

Expected: these fail:
- `test_prompt_after_banner_is_answered_once`, `test_prompt_that_never_clears_is_pressed_only_once`, `test_warmup_reports_unanswered_when_prompt_stays` (`TypeError` on `clear_seconds`, or an assertion);
- `test_wait_for_ready_codex_trust_hooks_and_marker`, `test_wait_for_ready_codex_folder_access_enables_hooks_answer`.

All other tests pass. Record the summary line.

**Step 3 (GREEN): implement.**

(a) In `main.py`, `_answer_codex_hooks_review`:

```python
def _answer_codex_hooks_review(tools, tmux, session: str, settle_seconds: float = 20.0,
                               clear_seconds: float = 10.0, sleep=time.sleep,
                               clock=time.monotonic) -> bool:
    """R7a/R7b: Codex can draw its "Hooks need review" prompt after the start-up banner, and
    the raw pane log renders it with cursor moves, so read the rendered screen. Press "2"
    (select "Trust all and continue") and Enter (confirm) once, then wait up to
    clear_seconds for the prompt to clear. Returns True only when the prompt was answered
    and left the screen; False when none appeared in settle_seconds or it never cleared."""
    def prompt_on_screen() -> bool:
        compact = re.sub(r"\s+", "", tools._codex_screen(session, None).lower())
        return "hooksneedreview" in compact and "trustallandcontinue" in compact

    deadline = clock() + settle_seconds
    while clock() < deadline:
        if prompt_on_screen():
            tmux.send_raw_keys(session, ["2", "Enter"])
            clear_deadline = clock() + clear_seconds
            while clock() < clear_deadline and prompt_on_screen():
                sleep(0.5)
            if prompt_on_screen():
                logger.warning("Codex hooks-review prompt still on screen after answering")
                return False
            return True
        sleep(1.0)
    return False
```

(b) `_warm_up_codex_hook_trust` gains `clear_seconds: float = 10.0` after `settle_seconds` and passes `clear_seconds=clear_seconds` to the helper call.

(c) In `orchestrator_mcp.py`, at `:2378-2380`:

```python
                        self.tmux.send_raw_keys(
                            session_name, ["2", "Enter"], ssh_host=ssh_host,
                        )
```

In the docstring at `:2334-2335`, "selects \"Trust all and continue\" once with its raw numeric shortcut." becomes "selects \"Trust all and continue\" once with its raw numeric shortcut and confirms it with Enter."

**Step 4: run GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_hook_trust_warmup.py tests/test_worker_adapter.py tests/test_codex_brain_hooks_canary.py -q
```

Expected: all pass. Record the summary line.

**Step 5: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_codex_hook_trust_warmup.py commander/tests/test_worker_adapter.py
```

Expected: no output.

---

## Task 2: Full suites over the whole change set (R11)

No tests required: this task only verifies and modifies no files. Its `allowed_files` lists the whole parity change set, so the end-of-plan restage and `/commit` cover all of it.

**Step 1:** `make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/ironclaude-hooks-trust-confirm-hooks.log 2>&1; echo "test-hooks exit=$?"`. Expected: `test-hooks exit=0`. On failure, run `grep -n FAIL` on the log and stop.

**Step 2:** run in the background with a Bash timeout of 1800000:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-hooks-trust-confirm-full.log 2>&1; echo "pytest exit=$?"
```

Then `tail -n 5` the log. Expected: the only allowed failure is `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd` (L-T2). Record the summary.

---

## Task 3: Commander restart and live acceptance (R11)

No unit tests: this is live verification. The commands are the follow-up plan's Task 3 Steps 4–13 (`docs/plans/2026-10-11-codex-brain-parity-followup.md`), with these changes:
- Step 3 is an operator launch. The agent records the time, then uses AskUserQuestion to ask the operator to run `make -C ~/Code/ironclaude/commander run` in their terminal and confirm.
- `-newer` anchors use `/tmp/ic-logs/ic-codex-hook-trust.log`. The warm-up rewrites this file at start-up, before the Brain starts.
- The expected warm-up line is `trust_prompt_answered=True`, with no `still on screen after answering` warning.
- On canary failure: run `make -C /Users/roberthyatt/Code/ironclaude/commander stop` immediately, record the evidence, and stop.

**Step 1:** `pgrep -fl "ironclaude.main"`. Expected: no output (exit 1).

**Step 2:** `wc -l /tmp/ic/daemon.log`. Record L.

**Step 3:** operator launch (AskUserQuestion), as above.

**Step 4:** `tail -n +<L+1> /tmp/ic/daemon.log`, repeated until either the Brain start or a canary failure appears. Expected:
- `Codex hook-trust warm-up finished ready=True trust_prompt_answered=True`;
- no `still on screen after answering`;
- `Brain SDK client started client=codex`;
- no `hooks are not running`;
- no provenance failure.

**Step 5:** `find /tmp/ic/codex-brain-gate -name hooks-live -newer /tmp/ic-logs/ic-codex-hook-trust.log`. Expected: exactly one path.

**Step 6:** the trust-entry grep from the follow-up plan's Task 3 Step 6. Expected: lines for `pre_tool_use:7`–`11`, `post_tool_use:7`–`8` and `user_prompt_submit:2`. `ls -la ~/.codex/config.toml` shows today's modification time.

**Steps 7–12:** as in the follow-up plan's Task 3 Steps 7–12. Use `/tmp/ic-logs/ic-codex-hook-trust.log` as the `-newer` anchor.

**Step 13:** write `docs/plans/2026-10-10-codex-brain-parity-live-verification.md`. It covers both earlier failed attempts (the 08:0x banner race and the 13:22 unconfirmed selection) and this attempt's Steps 1–12 output. Then:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-10-codex-brain-parity-live-verification.md docs/plans/2026-10-10-codex-brain-parity-design.md docs/plans/2026-10-11-codex-hooks-trust-confirm.md docs/plans/2026-10-11-codex-hooks-trust-confirm.plan.json
git -C /Users/roberthyatt/Code/ironclaude add worker/.codex-plugin/plugin.json
```

Expected: no output.
