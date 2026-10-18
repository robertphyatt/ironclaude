# Codex Hook Probe Implementation Plan (v1.1.16 L2a)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Answer Q1–Q4 from the design with evidence, and write a findings note:
- Q1: do IronClaude hooks run on Codex, and in what trust state?
- Q2: what PreToolUse `tool_name` does Codex send for a shell command and for an edit?
- Q3: what fields does the Stop hook input carry?
- Q4: is a Stop hook's `systemMessage` shown?

**Requirements:** `docs/plans/2026-10-02-codex-hook-probe-design.md` (`## Requirements` section; light loop)

**Architecture:** One investigation task with two headless `codex exec` turns in a scratch git repo in the session scratchpad.
- **Turn A** runs under the current trust state. Its positive control is the professional-mode guard's deterministic block of a shell command in an undecided session (`professional-mode-guard.sh:313-399`).
- **Turn B** adds a capture hook scoped to that one invocation: a scratch-repo `.codex/hooks.json` plus `--dangerously-bypass-hook-trust`. It records the real hook input and tests whether Codex shows a Stop `systemMessage`.

**Loop:** light. This is an investigation that touches no risk area. It writes one file in the repo. The scratch repo and its hook files live outside the repo.

**SCRATCH** = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe`

**PROBE** = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad`

Every command spells out the literal path.

**Invariants:**
- Use absolute paths; shell state does not persist between steps.
- No `2>/dev/null` on evidence commands.
- No global config edits. If `~/.codex/config.toml` changes during the probe, record the diff and do not hand-fix it.
- No push, no commits, kill nothing.
- Do not set `IRONCLAUDE_PM`: the undecided state is what guarantees the positive-control block.
- Never pass `--dangerously-bypass-hook-trust` to Turn A.

**Evidence already in hand** (include it in the note):
- The rollout `~/.codex/sessions/2026/10/02/rollout-2026-10-02T22-58-43-01a10020-fac1-7b31-b868-98f5fd1d3ed4.jsonl` contains the string `PROFESSIONAL MODE NOT SET` 2 times. The professional-mode guard ran as a Codex PreToolUse hook today.
- Operator screenshots taken at 23:07 show the IronClaude hooks switched on in Codex Settings → Hooks: PreToolUse 1–6, PostToolUse 1–6, Stop 1, SessionStart 1–3 and UserPromptSubmit 1–2, with no untrusted badge.
- In those screenshots, Stop runs `bash $HOME/.claude/ironclaude-hooks/get-back-to-work-claude.sh`, and SessionStart Hook 1 runs the versioned cache path `…/1.1.15+codex.20260930172837/hooks/session-init.sh`.

No tests required: this is an investigation, and its deliverable is the findings note.

---

## Task 1: Probe and write the findings

**Files:**
- Create: `docs/plans/2026-10-02-codex-hook-probe-findings.md`

**Step 1: History evidence (Q1)**

```bash
grep -r -l -F "hook_prompt" /Users/roberthyatt/.codex/sessions/2026/09 /Users/roberthyatt/.codex/sessions/2026/10
```

```bash
grep -r -o -E "ironclaude/[0-9.]+(\+codex\.[0-9]+)?/hooks/hooks.json" /Users/roberthyatt/.codex/sessions/2026/09 /Users/roberthyatt/.codex/sessions/2026/10
```

This form keeps the filenames. Record each version alongside its newest file.

```bash
grep -r -l -F "PROFESSIONAL MODE NOT SET" /Users/roberthyatt/.codex/sessions/2026/10
```

**Step 2: Trust state (Q1)**

Read `/Users/roberthyatt/.codex/config.toml` lines 140–230 and build a table of the IronClaude hook keys, recording `enabled` and `trusted_hash` for each.

Mark these keys:
- the 5 keys that lack `enabled`: `pre_tool_use:5`, `session_start:1`, `user_prompt_submit:2`, `subagent_stop:0` and `stop:0`;
- the stale `user_prompt_submit:2`, which has no counterpart in `hooks.json`, where only 2 UserPromptSubmit groups exist.

Cross-reference the operator screenshots: `pre_tool_use:5` is on in the UI, so an absent `enabled` means on. The hash scheme cannot be derived, so record it as unknown. Confirm that `[features]` has no `hooks=false` and that `~/.codex/hooks.json` does not exist.

**Step 3: Scratch repo and config snapshot**

```bash
mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe
```

```bash
git -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe init -q
```

```bash
cp /Users/roberthyatt/.codex/config.toml /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/config.before.toml
```

Expected: exit 0 for each command.

**Step 4: Turn A, with current trust and no bypass (Q1 positive control)**

Run in the background with a 10-minute cap:

```bash
codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe --skip-git-repo-check -s workspace-write --json --output-last-message /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/turnA-last.txt "Run exactly one shell command: echo ironclaude-hook-probe. If it is blocked, do not retry; reply DONE."
```

Redirect stdout and stderr to `PROBE/turnA.jsonl`.

How to read the result:
- `PROFESSIONAL MODE NOT SET` in `turnA.jsonl` or the Turn A rollout means **PreToolUse ran under the current trust state**.
- If that string is absent and the echo succeeded, the hook was skipped, or the `tool_name` is not in the matcher at `hooks.json:68`.
- If Codex asks for approval or hangs, record that and go to Step 8.

**Step 5: Turn B, with capture hooks scoped to this one invocation (Q2–Q4)**

First write `SCRATCH/.codex/capture.sh` with exactly this content:

```bash
INPUT=$(cat); mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe/capture; printf '%s' "$INPUT" > "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe/capture/$1-$(date +%s)-$$.json"; [ "$1" = Stop ] && printf '{"systemMessage":"IC-PROBE-STOP-SYSMSG"}'; exit 0
```

Then write `SCRATCH/.codex/hooks.json`. It needs a PreToolUse entry (`"matcher": "*"`) and a Stop entry, each running `bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe/.codex/capture.sh <Event>`. Use the same schema as the plugin's `hooks.json` (`{"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "..."}]}], "Stop": [{"hooks": [{"type": "command", "command": "..."}]}]}}`).

Run in the background with a 10-minute cap:

```bash
codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe --skip-git-repo-check -s workspace-write --json --dangerously-bypass-hook-trust "Run exactly one shell command: echo ironclaude-hook-probe. Then create probe.txt containing hi. If anything is blocked, do not retry; reply DONE."
```

Redirect stdout and stderr to `PROBE/turnB.jsonl`.

Turn B's bypass also bypasses trust for the plugin hooks, so **Q1 rests on Turn A alone**.

**Step 6: Read the results (Q1–Q4)**

1. Find the rollouts by `cwd`, so a run after midnight still works:
   ```bash
   grep -r -l -F '"cwd":"/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe"' /Users/roberthyatt/.codex/sessions/2026/10
   ```
2. **Q2:** read `tool_name` from each `SCRATCH/capture/PreToolUse-*.json` (use `jq -r .tool_name`). Expected: `Bash` for the echo and `apply_patch` for `probe.txt`.
   - If the guard's block short-circuits so that no capture is written, record "unobserved". The block message proves only that the tool is in the matcher at `hooks.json:68`.
   - The rollout's `"name"` field is the model-facing name and is **not** the answer to Q2.
3. **Q3:** list the keys of `SCRATCH/capture/Stop-*.json` (use `jq keys`). Compare them with the documented Stop fields: `permission_mode`, `agent_transcript_path`, `stop_hook_active`, `agent_id`, `last_assistant_message`.
4. **Q4:** search for the marker:
   ```bash
   grep -r -l -F "IC-PROBE-STOP-SYSMSG" /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/turnB.jsonl /Users/roberthyatt/.codex/sessions/2026/10
   ```

**Step 7: Side effects and the Claude half of Q3**

1. Diff the config:
   ```bash
   diff /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/config.before.toml /Users/roberthyatt/.codex/config.toml
   ```
   Record any change, including a residual `hooks.state` key. Report it; do not hand-fix it.
2. Record side effects:
   - any session row inserted by `session-init`;
   - `~/.claude/ironclaude-session-*.id` files created during the run;
   - whether `episodic-memory-sync.sh --background` launched `sync-cli.js`, found by an exact process listing; kill nothing.
3. For the Claude half of Q3, check the Claude Code hooks documentation for whether its Stop input carries `last_assistant_message`, and quote the source.

**Step 8: Write the findings note and stage it**

Create `docs/plans/2026-10-02-codex-hook-probe-findings.md`. For each of Q1–Q4, give:
- the answer;
- its evidence class: observed, documented, or unanswered with the reason;
- the commands and output that support it;
- its implication for L2b.

Also record:
- the evidence already in hand, listed at the top of this plan;
- the side effects;
- the observation that SessionStart Hook 1 uses a versioned cache path, which is an L2b candidate.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-02-codex-hook-probe-findings.md
```

Expected: exit 0.
