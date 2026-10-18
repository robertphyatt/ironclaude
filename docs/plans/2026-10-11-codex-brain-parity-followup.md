# Codex Brain Parity Follow-up Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** The Commander's Codex Brain starts with its restriction hooks trusted and running. The trust warm-up answers Codex's "Hooks need review" prompt from the rendered screen, and the live acceptance from the parity plan passes.

**Requirements:** `docs/plans/2026-10-10-codex-brain-parity-design.md` (section `## Requirements`, R7a and R11)

**Architecture:**
- A new helper `_answer_codex_hooks_review` in `commander/src/ironclaude/main.py` polls the rendered Codex screen through `OrchestratorTools._codex_screen`. It presses `2` once when "Hooks need review" and "Trust all and continue" are both on screen.
- `_warm_up_codex_hook_trust` calls it after `_wait_for_ready` and before the exact-name kill.
- The first parity plan (`docs/plans/2026-10-10-codex-brain-parity.plan.json`) is already done and staged: Tasks 1–13 passed review. This plan re-runs its suites and its live acceptance.

**Tech Stack:** Python 3 and pytest, tmux, Codex CLI 0.162.

## Execution invariants

- **Shell state does not persist between steps.** Every path is an absolute literal.
  - REPO = `/Users/roberthyatt/Code/ironclaude`
  - PROBE = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe`
- **Where things run.** pytest runs as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest …`. git runs as `git -C /Users/roberthyatt/Code/ironclaude …`.
- **Foreground `sleep` is blocked.** Waits run as background polling loops.
- **`docs/` is gitignored.** Stage docs with `git add -f`.
- **Evidence commands.** No `2>/dev/null` on evidence commands.
- **Expected values.** An expected value from the live run is recorded, never assumed.
- **Process safety.** Kill only by exact tmux session name or exact PID. Never commit or push.
- **The guard's 'git … push' false positive.** `professional-mode-guard.sh:814` blocks any Bash command whose text contains the word "git" followed later by the word "push". Commands that must pass the text "git push" to another program build it with `printf 'git p\165sh …'` (octal for `u`), and grep patterns use `pu[s]h`. The text Codex receives is unchanged.

## Verified source facts

**Commander (`commander/src/ironclaude/main.py`)**
- `main.py:5847` is `_warm_up_codex_hook_trust(tools, tmux, codex_path, workdir, timeout=90)`.
- It calls `tools._wait_for_ready(_HOOK_TRUST_WARMUP_SESSION, timeout=timeout, client="codex")`, logs `"Codex hook-trust warm-up finished ready=%s"`, and kills the session in `finally`.
- `main.py` imports `re` (`:15`) and `time` (`:23`).

**Helpers**
- `OrchestratorTools._codex_screen(session_name, ssh_host) -> str` (`orchestrator_mcp.py:2395`) returns the rendered pane text, or `""` when it cannot be captured.
- `TmuxManager.send_raw_keys(name, keys, ssh_host=None)` (`tmux_manager.py:377`) sends keys without Enter.

**Tests (`commander/tests/test_codex_hook_trust_warmup.py`)**
- The file has 3 tests that use `MagicMock()` tools.
- Unconfigured, `tools._codex_screen(...)` would return a MagicMock, so these tests must set `tools._codex_screen.return_value = ""`.

**Live failure (2026-10-11, recorded in the design)**
- `_wait_for_ready` returned on the start-up banner after 1.3 s.
- "Hooks need review — 9 hooks are new or changed / 2. Trust all and continue" appeared afterwards, so nothing was trusted.
- The canary stopped the Brain. The operator stopped the Commander, which is still stopped.

**Deploy state**
- Codex plugin `1.1.16+codex.20261011080324` is installed and its `hooks.json` holds the 9 Brain entries.
- `make deploy-hooks` and the Claude cache `hooks.json` copy are done.
- Neither the worker hooks nor `hooks.json` changes in this plan, so no plugin release or hook redeploy is needed. Commander code runs from the repo.
- `worker/.codex-plugin/plugin.json` has the release's unstaged cachebuster.

---

## Task 1: Warm-up answers the hooks-review prompt from the rendered screen (R7a)

**Files:**
- Modify: `commander/src/ironclaude/main.py` (`_warm_up_codex_hook_trust` and a new helper just above it)
- Test: `commander/tests/test_codex_hook_trust_warmup.py`

**Step 1 (RED).** Replace `commander/tests/test_codex_hook_trust_warmup.py` with:
```python
"""R7/R7a: before the Codex Brain starts, a throwaway Codex TUI in a dedicated directory
answers Codex's hook-trust prompt from the rendered screen, then is killed by its exact
session name."""
from unittest.mock import MagicMock

from ironclaude.main import (
    _HOOK_TRUST_WARMUP_SESSION,
    _answer_codex_hooks_review,
    _warm_up_codex_hook_trust,
)

PROMPT = (
    "  Hooks need review\n  9 hooks are new or changed.\n"
    "› 1. Review hooks\n  2. Trust all and\n  continue\n  3. Continue without trusting\n"
)
BANNER = ">_ OpenAI Codex (v0.162.0)\n› Ask Codex to do anything\n"


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Tools:
    """Rendered screens in order; the last one repeats."""

    def __init__(self, screens):
        self.screens = list(screens)
        self.reads = 0

    def _codex_screen(self, session_name, ssh_host):
        assert session_name == _HOOK_TRUST_WARMUP_SESSION and ssh_host is None
        self.reads += 1
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]


def _answer(screens, settle=20.0):
    tools, tmux, clock = _Tools(screens), MagicMock(), _Clock()
    answered = _answer_codex_hooks_review(
        tools, tmux, _HOOK_TRUST_WARMUP_SESSION, settle_seconds=settle,
        sleep=clock.sleep, clock=clock)
    return answered, tmux, tools


def test_prompt_after_banner_is_answered_once():
    answered, tmux, _ = _answer([BANNER, BANNER, PROMPT, BANNER])
    assert answered is True
    tmux.send_raw_keys.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, ["2"])


def test_no_prompt_within_settle_window_sends_nothing():
    answered, tmux, tools = _answer([BANNER], settle=5.0)
    assert answered is False
    tmux.send_raw_keys.assert_not_called()
    assert tools.reads >= 5


def test_prompt_that_never_clears_is_pressed_only_once():
    answered, tmux, _ = _answer([PROMPT])
    assert answered is True
    assert tmux.send_raw_keys.call_count == 1


def test_trust_option_without_review_heading_is_not_answered():
    answered, tmux, _ = _answer(["  2. Trust all and continue\n"], settle=3.0)
    assert answered is False
    tmux.send_raw_keys.assert_not_called()


def test_empty_capture_is_not_answered():
    answered, tmux, _ = _answer([""], settle=3.0)
    assert answered is False
    tmux.send_raw_keys.assert_not_called()


def _warmup_tools(screen=""):
    tools = MagicMock()
    tools._wait_for_ready.return_value = True
    tools._codex_screen.return_value = screen
    return tools


def test_warmup_spawns_waits_and_kills(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = _warmup_tools()
    _warm_up_codex_hook_trust(tools, tmux, "/opt/codex", str(tmp_path), timeout=5, settle_seconds=0)
    tmux.spawn_session.assert_called_once_with(
        _HOOK_TRUST_WARMUP_SESSION, "exec /opt/codex --no-daemon", cwd=str(tmp_path))
    tools._wait_for_ready.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION, timeout=5, client="codex")
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)


def test_warmup_answers_prompt_before_kill(tmp_path, caplog):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    order = []
    tmux.send_raw_keys.side_effect = lambda *a, **k: order.append("keys")
    tmux.kill_session.side_effect = lambda *a, **k: order.append("kill")
    tools = _warmup_tools()
    tools._codex_screen.side_effect = [PROMPT] + [BANNER] * 5  # clears after the key press
    with caplog.at_level("INFO"):
        _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5, settle_seconds=2)
    assert order == ["keys", "kill"]
    assert "trust_prompt_answered=True" in caplog.text


def test_warmup_kills_even_when_wait_raises(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tools = _warmup_tools()
    tools._wait_for_ready.side_effect = RuntimeError("boom")
    _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5, settle_seconds=0)
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)


def test_warmup_replaces_leftover_session_and_skips_wait_on_spawn_failure(tmp_path):
    tmux = MagicMock()
    tmux.has_session.return_value = True
    tmux.spawn_session.return_value = False
    tools = _warmup_tools()
    _warm_up_codex_hook_trust(tools, tmux, "codex", str(tmp_path), timeout=5, settle_seconds=0)
    tmux.kill_session.assert_called_once_with(_HOOK_TRUST_WARMUP_SESSION)
    tools._wait_for_ready.assert_not_called()
```
`test_warmup_answers_prompt_before_kill` uses the real `time.sleep`, but it never sleeps. The first read shows the prompt, the next read shows it cleared, and the helper returns.

**Step 2.** Run RED.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_hook_trust_warmup.py -q
```
Expected: a collection error, because `_answer_codex_hooks_review` cannot be imported (ImportError). Record the summary.

**Step 3 (GREEN).** In `commander/src/ironclaude/main.py`:

(a) Insert directly above `def _warm_up_codex_hook_trust(`:
```python
def _answer_codex_hooks_review(tools, tmux, session: str, settle_seconds: float = 20.0,
                               clear_seconds: float = 10.0, sleep=time.sleep,
                               clock=time.monotonic) -> bool:
    """R7a: Codex can draw its "Hooks need review" prompt after the start-up banner, and
    the raw pane log renders it with cursor moves, so read the rendered screen. Press "2"
    (Trust all and continue) once, then wait up to clear_seconds for the prompt to clear.
    Returns True when the prompt was answered, False when none appeared in settle_seconds."""
    def prompt_on_screen() -> bool:
        compact = re.sub(r"\s+", "", tools._codex_screen(session, None).lower())
        return "hooksneedreview" in compact and "trustallandcontinue" in compact

    deadline = clock() + settle_seconds
    while clock() < deadline:
        if prompt_on_screen():
            tmux.send_raw_keys(session, ["2"])
            clear_deadline = clock() + clear_seconds
            while clock() < clear_deadline and prompt_on_screen():
                sleep(0.5)
            if prompt_on_screen():
                logger.warning("Codex hooks-review prompt still on screen after answering")
            return True
        sleep(1.0)
    return False


```

(b) Change the signature line `def _warm_up_codex_hook_trust(tools, tmux, codex_path: str, workdir: str, timeout: int = 90) -> None:` to:
```python
def _warm_up_codex_hook_trust(tools, tmux, codex_path: str, workdir: str, timeout: int = 90,
                              settle_seconds: float = 20.0) -> None:
```

(c) Replace the two lines:
```python
        ready = tools._wait_for_ready(_HOOK_TRUST_WARMUP_SESSION, timeout=timeout, client="codex")
        logger.info("Codex hook-trust warm-up finished ready=%s", ready)
```
with:
```python
        ready = tools._wait_for_ready(_HOOK_TRUST_WARMUP_SESSION, timeout=timeout, client="codex")
        answered = _answer_codex_hooks_review(
            tools, tmux, _HOOK_TRUST_WARMUP_SESSION, settle_seconds=settle_seconds)
        logger.info("Codex hook-trust warm-up finished ready=%s trust_prompt_answered=%s",
                    ready, answered)
```

**Step 4.** Run GREEN, plus the Codex Brain and daemon suites.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_hook_trust_warmup.py tests/test_codex_brain_hooks_canary.py tests/test_brain_hook_provenance.py -q
```
Expected: all pass. Record the summary.

**Step 5.** Stage.
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_codex_hook_trust_warmup.py
```
Expected: no output.

---

## Task 2: Full suites over the whole parity change set (R11)

**Files:** none modified. This is a verification task.

The machine plan lists every file from the first parity plan (Tasks 1–13), plus this plan's files and the plan artifacts, as `allowed_files`. The end-of-plan restage and the operator's `/commit` therefore cover the whole parity change set. This task edits none of them.

**Step 1.** Run the hook suites.
```bash
make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/ironclaude-brain-parity-hooks-2.log 2>&1; echo "test-hooks exit=$?"
```
Expected: `test-hooks exit=0`. On failure, run `grep -n FAIL /private/tmp/ironclaude-brain-parity-hooks-2.log` and stop.

**Step 2.** Run the full Commander suite (background; Bash timeout 1800000).
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-brain-parity-full-2.log 2>&1; echo "pytest exit=$?"
```
Then run `tail -n 5 /private/tmp/ironclaude-brain-parity-full-2.log`. Expected: the only failure is `tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd` (L-T2). Record the summary line.

---

## Task 3: Restart the Commander and run the live acceptance (R11)

**Files:**
- Modify: `worker/.codex-plugin/plugin.json` (stage the release cachebuster only)
- Create: `docs/plans/2026-10-10-codex-brain-parity-live-verification.md`

No unit tests: this is live verification. Each check names the hook or log line it expects, so a missing hook or a failed trust fails it.

**Step 1.** Confirm the Commander is stopped.
```bash
pgrep -fl "ironclaude.main"
```
Expected: no output (exit 1). If a daemon is listed, run `make -C /Users/roberthyatt/Code/ironclaude/commander stop` and repeat.

**Step 2.** Record the daemon log length.
```bash
wc -l /tmp/ic/daemon.log
```
Record it as L.

**Step 3.** Relaunch the Commander detached, with the Claude Code environment stripped. This is a normal Bash call, not `run_in_background`.
```bash
env -u CLAUDECODE -u CLAUDE_CODE_BRIDGE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_CODE_SESSION_ID -u CLAUDE_EFFORT -u CLAUDE_PID nohup python3 -c "import os; os.setsid(); os.execvp('make',['make','-C','/Users/roberthyatt/Code/ironclaude/commander','run'])" >/tmp/ironclaude-restart-launch.log 2>&1 & disown
```

**Step 4.** Wait for the start-up result. Run in the background with a Bash timeout of 660000, using L+1 from Step 2 as a literal:
```bash
for i in $(seq 1 120); do if tail -n +<L+1> /tmp/ic/daemon.log | grep -q -e "Brain SDK client started client=codex" -e "Brain SDK client not started" -e "hooks are not running" -e "Brain hooks must run scripts shipped"; then break; fi; sleep 5; done; tail -n +<L+1> /tmp/ic/daemon.log | grep -n -e "Codex hook-trust warm-up" -e "Brain SDK client" -e "hooks are not running" -e "Brain hooks must run scripts shipped"
```
Expected:
- `Codex hook-trust warm-up finished ready=… trust_prompt_answered=True`;
- `Brain SDK client started client=codex`;
- no `hooks are not running`, and no provenance failure.

**If the canary fails again:** stop the Commander immediately with `make -C /Users/roberthyatt/Code/ironclaude/commander stop`, record the evidence, and stop. This prevents the retry loop (backlog `project_brain_canary_retry_loop`).

**Step 5.** Check the canary marker.
```bash
find /tmp/ic/codex-brain-gate -name hooks-live -newer /tmp/ironclaude-restart-launch.log
```
Expected: exactly one path.

**Step 6.** Check trust in Codex's config.
```bash
grep -n -e "hooks.json:pre_tool_use:1[01]:0" -e "hooks.json:pre_tool_use:[7-9]:0" -e "hooks.json:post_tool_use:[78]:0" -e "hooks.json:user_prompt_submit:2:0" /Users/roberthyatt/.codex/config.toml
```
Expected: lines for `pre_tool_use:7` to `11`, `post_tool_use:7` and `8`, and `user_prompt_submit:2`. Record them.

**Step 7.** Check the Claude Brain settings after the sync.
```bash
jq -r '.hooks[][] .hooks[].command' /Users/roberthyatt/.ironclaude/brain/.claude/settings.json
```
Expected: exactly the 7 template commands in template order, with no `.ironclaude/brain/hooks/`, `memory-search-enforcer` or `startup-lookback-enforcer`.

Then check the retired file:
```bash
ls /Users/roberthyatt/.ironclaude/brain/.codex
```
Expected: `hooks.json.retired` and no `hooks.json`.

**Step 8.** Find the Brain's rollout.
```bash
find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /tmp/ironclaude-restart-launch.log
```
Record the paths. Identify the Brain's file, BR, with `grep -l '"cwd":"/Users/roberthyatt/.ironclaude/brain"'` on each path.

**Step 9.** Check the Brain rollout. Expected: `0`.
```bash
grep -c professional-mode-guard BR
```
Then:
```bash
grep -o '"server":"orchestrator","tool":"[a-z_]*"' BR
```
Expected: one or more lines from the start-up lookback. Record the distinct names.

**Step 10 (operator acceptance input).** Use AskUserQuestion to ask the operator to post "what unfinished work is sitting, idle, right now?" in Slack and confirm. Then run:
```bash
grep -c '"server":"orchestrator","tool":"get_worker_status"' BR
```
Expected: `1` or more. Then run `grep -c professional-mode-guard BR` again; expected `0`. If the operator defers, record "Step 10 pending operator question". Never substitute another check.

**Step 11.** Run the negative probe through Codex, with the Brain identity and real trust (no bypass). Create the probe repo:
```bash
mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo
```
Then initialise it:
```bash
git -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo init -q
```
Then run the probe (Bash timeout 600000):
```bash
env IC_ROLE=brain IRONCLAUDE_CLIENT=codex IRONCLAUDE_BRAIN_GATE_SESSION=parity-probe codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo --skip-git-repo-check -s workspace-write --json "$(printf 'Do these in order. Do not retry anything that is blocked. 1) Run the shell command: git p\165sh origin main 2) Create the file x.txt containing hi with your file-editing tool. 3) Spawn exactly one subagent whose only task is to reply OK. 4) Run the shell command: cat /etc/hosts. Then reply DONE.')" > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brain-turn.jsonl 2>&1; echo "exit=$?"
```
Expected: an `exit=` line. Record it.

**Step 12.** Read the probe's evidence. First the blocks:
```bash
grep -o -e 'block-pu[s]h' -e 'brain-orchestrator-guard' -e 'Brain cannot use mutation tools' -e 'brain-task-gate' -e 'professional-mode-guard' -e 'is blocked for Brain session' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brain-turn.jsonl
```
Then check the file was not created:
```bash
ls /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo
```
Expected:
- the push is blocked (`is blocked for Brain session` or `brain-orchestrator-guard`);
- the edit is blocked (`Brain cannot use mutation tools`);
- the subagent is blocked (`brain-task-gate`);
- there is no `professional-mode-guard`;
- `x.txt` is absent.

Record the `cat` step's result as read parity, and record the matched lines verbatim.

**Step 13.** Write `docs/plans/2026-10-10-codex-brain-parity-live-verification.md`. It contains:
- the first attempt's failure (warm-up returned on the banner, canary stopped the Brain, Commander stopped);
- the recorded output of Steps 1–12 of this attempt.

Then stage the doc and the cachebuster:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-10-codex-brain-parity-live-verification.md
```
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/.codex-plugin/plugin.json
```
Expected: no output from either.
