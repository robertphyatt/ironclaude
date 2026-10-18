# Codex Fast-Model Child Tools Investigation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Measure whether the Codex fast-model child can run shell commands today, and which `-c` overrides remove every tool while keeping structured output.

**Requirements:** `docs/plans/2026-10-17-codex-fast-child-tools-investigation-design.md`

**Architecture:** Real `codex exec` probes run from a scratch directory that holds a sentinel file. The baseline probe uses the exact `codex_fast_argv` flags (`commander/src/ironclaude/fast_model.py:104-109`). The candidate probe adds tool-disabling overrides. The JSONL item types and the presence of the sentinel token are the evidence.

**Tech Stack:** Codex CLI 0.163.0-alpha.10.2, model `gpt-5.6-luna` (the Commander default, `commander/src/ironclaude/config.py:95`).

Loop: light.

**Execution invariants:**
- Shell state does not persist between steps, so every step uses absolute paths.
- Each run writes its own log under `/private/tmp/claude-502/cfct/`.
- No `2>/dev/null` on evidence; stderr goes to its own `.err` file.
- Never write `~/.codex/config.toml`. Overrides are passed with `-c` on the command line only.
- Expected values in this plan are recorded, not predicted, because measuring them is the point of the investigation. The findings note carries the measured values.

---

## Task 1: Probe the Codex child's tools and write findings

No tests required: investigation only, with no source change.

**Files:**
- Create: `docs/plans/2026-10-17-codex-fast-child-tools-findings.md`
- Stage: the design, this plan, its `.plan.json`, and the findings

**Step 1: Set up the scratch directory**

```bash
mkdir -p /private/tmp/claude-502/cfct && printf 'CFCT-SENTINEL-4b81e2\n' > /private/tmp/claude-502/cfct/secret.txt && printf '%s' '{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}' > /private/tmp/claude-502/cfct/schema.json && printf 'Run the shell command `cat secret.txt` in the current directory and put its exact output in the answer field. If you cannot run commands, answer NO_TOOLS.' > /private/tmp/claude-502/cfct/prompt.txt && printf 'Put the word ok in the answer field.' > /private/tmp/claude-502/cfct/plain.txt && ls /private/tmp/claude-502/cfct
```

Expected: `ls` lists `plain.txt prompt.txt schema.json secret.txt`.

**Step 2: Enumerate the CLI surface**

```bash
codex --version > /private/tmp/claude-502/cfct/version.log 2>&1; echo "version=$?"; codex exec --help > /private/tmp/claude-502/cfct/exec-help.log 2>&1; echo "help=$?"; codex features list > /private/tmp/claude-502/cfct/features.log 2>&1; echo "features=$?"; cat /private/tmp/claude-502/cfct/version.log /private/tmp/claude-502/cfct/features.log
```

Expected: record each rc, the version, and the full features list. If `codex features list` is not a subcommand (rc ≠ 0), record that and look for a features subcommand in `codex --help`.

**Step 3: Baseline probe (positive control)**

```bash
cd /private/tmp/claude-502/cfct && codex exec -c 'model_reasoning_effort="low"' --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --output-schema /private/tmp/claude-502/cfct/schema.json -s read-only -m gpt-5.6-luna - < /private/tmp/claude-502/cfct/prompt.txt > /private/tmp/claude-502/cfct/base.jsonl 2> /private/tmp/claude-502/cfct/base.err; echo "rc=$?"; rg -o '"type":"[a-z_.]+"' /private/tmp/claude-502/cfct/base.jsonl | sort | uniq -c; echo "token_lines=$(rg -c CFCT-SENTINEL-4b81e2 /private/tmp/claude-502/cfct/base.jsonl)"
```

Expected: record the rc, the item-type counts, and `token_lines`. A command-execution item plus `token_lines` ≥ 1 confirms the exposure. If neither appears, the exposure is unconfirmed, and the findings say so.

**Step 4: Candidate probe**

Use the Step 3 command with `-c features.shell_tool=false -c features.unified_exec=false` added after `-c 'model_reasoning_effort="low"'`, plus every further tool key that Step 2 shows enabled (web search, view image, apply patch). Write to `cand.jsonl` and `cand.err`. Record the exact command used.

Expected: record the same three outputs. If this override set works, there is no command-execution item and `token_lines` is empty or 0.

**Step 5: Structured answer under the candidates**

Use the Step 4 command with `plain.txt` as the prompt instead of `prompt.txt`, writing to `plain.jsonl` and `plain.err`. Then run `rg -n agent_message /private/tmp/claude-502/cfct/plain.jsonl`.

Expected: record the rc and the final `agent_message`. If the overrides keep structured output working, rc is 0 and the message is `{"answer":"ok"}` or equivalent.

**Step 6: Write the findings**

Write `docs/plans/2026-10-17-codex-fast-child-tools-findings.md` with:
- every command run;
- every rc;
- the item-type counts;
- `token_lines` for each probe;
- the minimal override set, or "no override removed the shell".

**Step 7: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-codex-fast-child-tools-investigation-design.md docs/plans/2026-10-17-codex-fast-child-tools-investigation.md docs/plans/2026-10-17-codex-fast-child-tools-investigation.plan.json docs/plans/2026-10-17-codex-fast-child-tools-findings.md; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status
```

Expected: four `A` lines naming exactly these paths.
