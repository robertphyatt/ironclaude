# Codex Brain: No Subagents (R12) and Tool Inventory (R13) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Enforce "Brain does not spawn subagents" on the Codex Brain in two ways: the app-server never offers the tools (`agents.enabled=false`), and `brain-task-gate` blocks the namespaced `collaborationspawn_agent`. Then inventory the Brain's tools for the operator.

**Requirements:** `docs/plans/2026-10-10-codex-brain-parity-design.md`, R12, R13 and finding F1. The operator chose "A+B" and "Inventory now" on 2026-10-11.

**Architecture:**
- **A:** add one `-c agents.enabled=false` pair to `CodexBrainClient._app_server_argv`.
- **B:** add the literal `collaborationspawn_agent` to the gate's plugin matcher, and have `brain-task-gate.sh` block every tool name ending in `spawn_agent`.
- **Deploy:** release the Codex plugin. The Commander restart's warm-up re-trusts the changed matcher.
- **Verify live:** the Brain rollout has no v2 collaboration tools; probe A (Brain flags) has no spawn tool and gives the inventory; probe B (without A) is blocked by the gate.

**Tech Stack:** Python 3 (Commander), pytest, bash hook tests, jq, Codex CLI 0.162.0-alpha.17.2.

**Loop:** full. This is guard/permission code and Brain enforcement.

## Execution invariants

- Shell state does not persist between steps. Use absolute paths.
- Bash cwd is `commander/`. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- zsh `nomatch`: quote globs.
- No foreground `sleep`.
- `docs/` is gitignored, so stage docs with `git add -f`.
- Run pytest with `PYTHONUNBUFFERED=1`.
- Kill processes only by exact PID or exact session name, or with `make stop`.
- Guard false positive: build any "git push" text with `printf 'git p\165sh …'`, and grep `pu[s]h`.
- The operator launches the Commander from their own terminal. The agent-side relaunch command is blocked by a safety filter and is never re-sent.
- `PROBE=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe` is used as a literal path in each command, not as a shell variable.

---

## Task 1: Brain app-server argv disables agents (R12 A)

**Files:**
- Modify: `commander/src/ironclaude/codex_brain_client.py:282-291` (`_app_server_argv`)
- Test: `commander/tests/test_codex_brain_client.py:43-54`

**Step 1 (RED).** In `test_app_server_argv_forces_readonly_and_on_request`, insert after `"-c", 'model_reasoning_effort="high"',`:

```python
            "-c", "agents.enabled=false",
```

Add one line to the class docstring (`:40-42`): "agents.enabled=false keeps Codex's collaboration (subagent) tools away from the Brain (R12)."

**Step 2.** Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py -q
```

Expected: exactly 1 failure, `TestSpawnHardening::test_app_server_argv_forces_readonly_and_on_request` (list mismatch). Record the summary.

**Step 3 (GREEN).** In `_app_server_argv`, insert after `"-c", f'model_reasoning_effort="{effort}"',`:

```python
            # R12: no subagents for the Brain. agents.enabled=false makes Codex's
            # multi-agent version Disabled ahead of the model catalog (which gives
            # gpt-5.6-terra the v2 "collaboration" tools), so spawn_agent is never offered.
            "-c", "agents.enabled=false",
```

**Step 4.** Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_codex_brain_client.py tests/test_codex_brain_hooks_canary.py -q
```

Expected: all pass. Record the summary.

**Step 5.** Stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/codex_brain_client.py commander/tests/test_codex_brain_client.py
```

Expected: no output.

---

## Task 2: brain-task-gate catches the namespaced spawn (R12 B)

**Files:**
- Modify: `worker/hooks/hooks.json:131` (matcher)
- Modify: `commander/hooks/brain-task-gate.sh:2`, `:24`, `:44-46`
- Test: `commander/hooks/tests/test-brain-task-gate.sh`
- Test: `worker/hooks/tests/test-hooks-json-brain-entries.sh:17`

**Step 1 (RED).** Edit the tests:
- `test-brain-task-gate.sh`: after line 56, add

```bash
# Codex multi-agent v2 tools live in the "collaboration" namespace; Codex hands hooks
# the flat name namespace+name (registry.rs function_hook_tool_name / flat_tool_name).
assert_eq "namespaced collaborationspawn_agent blocked" "2" "$(feed collaborationspawn_agent OMIT)"
```

  After line 74, add

```bash
assert_eq "no-logger: collaborationspawn_agent blocked" "2" "$(nofeed collaborationspawn_agent general-purpose)"
```

- `test-hooks-json-brain-entries.sh:17`: the matcher becomes `"Agent|Task|spawn_agent|collaborationspawn_agent"`.

**Step 2.** Run each:

```bash
bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-task-gate.sh
```

Expected: `13 passed, 2 failed`. The failures are the two `collaborationspawn_agent` cases.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-hooks-json-brain-entries.sh
```

Expected: exactly 1 FAIL line, `FAIL: PreToolUse Brain tail`.

**Step 3 (GREEN).**
- `worker/hooks/hooks.json:131`: `"matcher": "Agent|Task|spawn_agent"` becomes `"matcher": "Agent|Task|spawn_agent|collaborationspawn_agent"`. Change nothing else; it is still literal-only, so it keeps exact matching.
- `brain-task-gate.sh`:
  - Line 2 comment: `"spawn_agent" on Codex` becomes `"spawn_agent|collaborationspawn_agent" on Codex`.
  - Line 24: `if [[ "$_TN" == "spawn_agent" ]]; then` becomes `if [[ "$_TN" == *spawn_agent ]]; then`.
  - Line 46: `if [[ "$TOOL_NAME" == "spawn_agent" ]]; then` becomes `if [[ "$TOOL_NAME" == *spawn_agent ]]; then`.
  - Replace the comment above it (`:44-45`) with:

```bash
# Codex's native subagent tool: "spawn_agent" in the default/multi_agent_v1 namespace,
# or namespace+name (e.g. "collaborationspawn_agent", multi-agent v2) otherwise. The
# Codex Brain has no memory-search agent type (it searches episodic memory through MCP
# directly), so every spawn is blocked.
```

**Step 4.** Rerun both commands from Step 2. Expected: `15 passed, 0 failed`, and `test-hooks-json-brain-entries.sh` shows no FAIL line and exits 0.

**Step 5.** Stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/hooks.json commander/hooks/brain-task-gate.sh commander/hooks/tests/test-brain-task-gate.sh worker/hooks/tests/test-hooks-json-brain-entries.sh
```

Expected: no output.

---

## Task 3: Full suites over the whole change set

No tests required: this task only verifies and modifies no files. Its `allowed_files` lists the whole parity change set, so the end-of-plan restage and `/commit` cover all of it.

**Step 1:** `make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/ironclaude-no-subagents-hooks.log 2>&1; echo "test-hooks exit=$?"`. Expected: `test-hooks exit=0`.

**Step 2:** run in the background with a Bash timeout of 1800000:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-no-subagents-full.log 2>&1; echo "pytest exit=$?"
```

Wait for it with a foreground `until tail -n 1 <log> | grep -qE "passed|failed|error"; do sleep 5; done` loop. Expected: the only failure is L-T2 (`test_execution_complete_restage_command_stages_from_non_root_cwd`). Record the summary.

---

## Task 4: Deploy, restart, live acceptance and inventory (R12, R13)

No unit tests: this task deploys and verifies live.

**Step 1.** `make -C /Users/roberthyatt/Code/ironclaude codex-plugin-release`. Expected: exit 0, and it prints the installed plugin root. Record the root. If it isn't printed, use the newest version directory under `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/`.

**Step 2.** `grep -c collaborationspawn_agent <plugin root>/hooks/hooks.json`. Expected: `1`.

**Step 3.** `make -C /Users/roberthyatt/Code/ironclaude deploy-hooks`. It deploys only `worker/hooks/*.sh` (`Makefile:61`). The Brain's copy of `commander/hooks/*.sh` is refreshed only at Commander start (`_sync_brain_settings_hooks`, `main.py:5733-5736`).

Then record the before-values:
- `grep -c '== \*spawn_agent' /Users/roberthyatt/.claude/ironclaude-hooks/brain-task-gate.sh` → `0` (old gate still deployed).
- `grep -n -A2 "hooks.json:pre_tool_use:11:0" /Users/roberthyatt/.codex/config.toml` → record the `trusted_hash` H0.

**Step 4.** `make -C /Users/roberthyatt/Code/ironclaude/commander stop`, then `pgrep -fl "ironclaude.main"`. Expected: `Daemon (PID …) stopped`, then pgrep exit 1. Then run `wc -l /tmp/ic/daemon.log` and record L.

**Step 5.** Operator launch: AskUserQuestion asking the operator to run `make -C ~/Code/ironclaude/commander run` and confirm.

**Step 6.** Wait for the start-up result:

```bash
until tail -n +<L+1> /tmp/ic/daemon.log | grep -qE "Brain SDK client (started|not started)|hooks are not running"; do sleep 3; done; tail -n +<L+1> /tmp/ic/daemon.log | grep -v slack_bolt
```

Expected:
- `Codex hook-trust warm-up finished ready=True trust_prompt_answered=True`;
- `Brain SDK client started client=codex`;
- no `hooks are not running`;
- no `still on screen`.

On a canary failure: `make … stop`, record the evidence, and stop.

**Step 7.** Trust and canary checks:
- `find /tmp/ic/codex-brain-gate -name hooks-live -newer /tmp/ic-logs/ic-codex-hook-trust.log` → exactly one path.
- `grep -n -A2 "hooks.json:pre_tool_use:11:0" /Users/roberthyatt/.codex/config.toml` → one entry whose `trusted_hash` H1 differs from H0. That shows the changed gate matcher was re-trusted.
- `grep -c '== \*spawn_agent' /Users/roberthyatt/.claude/ironclaude-hooks/brain-task-gate.sh` → `2`. The new gate was deployed at Commander start; it was `0` in Step 3.
- `ls -la /Users/roberthyatt/.codex/config.toml` → mtime later than the launch time recorded in Step 4.

**Step 8.** Find the live Brain rollout BR: the newest file under `/Users/roberthyatt/.codex/sessions` that is `-newer /tmp/ic-logs/ic-codex-hook-trust.log` and contains `"cwd":"/Users/roberthyatt/.ironclaude/brain"`. Then:
- `grep -c "concurrency slots" BR` → `0` (the v2 multi-agent role prompt is gone);
- `grep -c '"namespace":"collaboration"' BR` → `0`;
- `grep -c professional-mode-guard BR` → `0`.

**Step 9.** Probe A (capability off, with the Brain's flags; this is also the R13 inventory):

```bash
env IC_ROLE=brain IRONCLAUDE_CLIENT=codex IRONCLAUDE_BRAIN_GATE_SESSION=parity-probe-a codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo --skip-git-repo-check -s read-only -c agents.enabled=false -c 'model="gpt-5.6-terra"' --json "List, verbatim and one per line, the exact name of every tool available to you, including every tools.* function callable inside your exec tool and every MCP or plugin tool. Then, if you have any tool for spawning a subagent, spawn exactly one whose only task is to reply OK; if you have none, say NO_SPAWN_TOOL. Then reply DONE." > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/probe-a.jsonl 2>&1; echo "exit=$?"
```

(Bash timeout 600000.) Then find probe A's rollout (newest under `/Users/roberthyatt/.codex/sessions`, containing `IRONCLAUDE_BRAIN_GATE_SESSION` or the probe cwd). Expected:
- `grep -c '"namespace":"collaboration"'` → 0;
- `grep -c "concurrency slots"` → 0;
- the agent's final message contains `NO_SPAWN_TOOL`.

Record the full tool list verbatim. That is the R13 inventory. If `codex exec` rejects `-c`, that is a plan defect, not a product failure: stop and report.

**Step 10.** Probe B (hook alone, without `agents.enabled=false`):

```bash
env IC_ROLE=brain IRONCLAUDE_CLIENT=codex IRONCLAUDE_BRAIN_GATE_SESSION=parity-probe-b codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/brainrepo --skip-git-repo-check -s read-only -c 'model="gpt-5.6-terra"' --json "Spawn exactly one subagent whose only task is to reply OK. Do not retry if blocked. Then reply DONE." > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/brain-parity-probe/probe-b.jsonl 2>&1; echo "exit=$?"
```

Then `grep -c "the Brain must not spawn subagents" <probe-b.jsonl>`. Expected: ≥ 1 (Codex's stderr ERROR line carries the hook message). No child rollout appears for probe B, and no `"task_name":"/root/` output.

**Step 11.** Write the R12/R13 section into `docs/plans/2026-10-10-codex-brain-parity-live-verification.md`. Mark F1 resolved, with the Step 1–10 output verbatim. Add the R13 inventory table: tool, source, present in the Claude Brain (y/n), recommendation. Then:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-10-codex-brain-parity-live-verification.md docs/plans/2026-10-10-codex-brain-parity-design.md docs/plans/2026-10-11-codex-brain-no-subagents.md docs/plans/2026-10-11-codex-brain-no-subagents.plan.json
git -C /Users/roberthyatt/Code/ironclaude add worker/.codex-plugin/plugin.json
```

Expected: no output.

**Step 12.** Report the R13 inventory to the operator with AskUserQuestion: which extra tools to remove from the Brain, if any. No code changes in this plan.
