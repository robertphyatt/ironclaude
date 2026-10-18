# v1.2.0 Security Fixes Findings

## Abbreviation probe

Source: /private/tmp/claude-502/taf3/t1-abbrev.log

```
usage: git diff [<options>] [<commit>] [--] [<path>...]
   or: git diff [<options>] --cached [--merge-base] [<commit>] [--] [<path>...]
   or: git diff [<options>] [--merge-base] <commit> [<commit>...] <commit> [--] [<path>...]
   or: git diff [<options>] <commit>...<commit> [--] [<path>...]
   or: git diff [<options>] <blob> <blob>
   or: git diff [<options>] --no-index [--] <path> <path>

common diff options:
  -z            output diff-raw with lines terminated with NUL.
  -p            output patch format.
  -u            synonym for -p.
  --patch-with-raw
                output both a patch and the diff-raw format.
  --stat        show diffstat instead of patch.
  --numstat     show numeric diffstat instead of patch.
  --patch-with-stat
                output a patch and prepend its diffstat.
  --name-only   show only names of changed files.
  --name-status show names and status of changed files.
  --full-index  show full object name on index lines.
  --abbrev=<n>  abbreviate object names in diff-tree header and diff-raw.
  -R            swap input file pairs.
  -B            detect complete rewrites.
  -M            detect renames.
  -C            detect copies.
  --find-copies-harder
                try unchanged files as candidate for copy detection.
  -l<n>         limit rename attempts up to <n> paths.
  -O<file>      reorder diffs according to the <file>.
  -S<string>    find filepair whose only one side contains the string.
  --pickaxe-all
                show all files diff when -S is used and hit is found.
  -a  --text    treat all files as text.

fatal: unrecognized argument: --outp=/private/tmp/claude-502/taf3/t1-abbrev-log.out
```

## Full suites

- Commander: `4126 passed in 490.90s (0:08:10)` (suite=0)
- Hooks: `make_rc=0`, 60 suite headers; last line `8 passed, 0 failed`
- State-manager: `npm=0`; ` Test Files  17 passed (17)`; `      Tests  273 passed (273)`

## Release commit message addition

Security fixes folded into v1.2.0 (2026-10-18). Both Brains refuse git commands that would write a file through --output (git log, git diff and git show), and both Brains and the worker read-only check refuse brace groups the shell would expand (a comma or ..), and Brain git commands refuse glob characters, because the shell rewrites an approved command before git sees it; and both Brains' Python checks allow git branch only for listing, never to create, delete, rename, copy or force a branch. The Codex Brain refuses to start when its MCP inventory holds a running server outside its own set (orchestrator, episodic-memory, research and ollama; servers Codex reports as disabled do not count), and its tool trim keeps research and ollama only when IronClaude's own server file exists. A worker's usage quarantine lasts at most 7 days after the limit event, however late the reported reset.

## Remediation suites

- Commander: 4154 passed in 681.93s (0:11:21)
- Hooks: make_rc=0, 60 suite headers
- State-manager: Test Files  17 passed (17); Tests  273 passed (273)

## Post-deploy verification (f53b78c)

Plan `docs/plans/2026-10-18-v120-live-checks.md` (lineage 203). Start marker `/private/tmp/claude-502/taf6/r6-start`, timestamp `2026-10-18 17:33:49`; daemon.log lines count only at or after it.

| Step | Check | Verdict |
|---|---|---|
| 1 | Deploy matches f53b78c | PASS |
| 2 | A1 locked reviewer tools | PASS (operator ruling on `advisor`) |
| 3 | Codex grader on newest Sol | PASS |
| 4 | Commander on Codex start | PASS |
| 5 | Codex Brain rollout has no tool errors | PASS (substitute command, see below) |
| 6 | Per-call tiering 1b / 2 / 3 | 1b PASS; 2 UNVERIFIED-LIVE; 3 PASS |
| 7 | R4b defects 2, 5, 9 | UNVERIFIED-LIVE (operator skipped) |

### Step 1
```
2026-10-18 17:33:49
f53b78c Release v1.2.0: one IronClaude for Claude and Codex, right-sized model checks, and tool-free children
guard_cmp=0
predicate_cmp=0
agent_cmp=0
gate_cmp=0
canary_cmp=0
validator_cmp=0
drift_cmp=0
codex_drift_cmp=0
1.2.0+codex.20261018224918
HEAD@{1}
a
b
```

### Step 2 (A1)
- `ironclaude:reviewer` (haiku), tool list: `Read, Grep, Glob, advisor`.
- `ironclaude:reviewer` (haiku), shell probe: `NO-SHELL`.
- Control `general-purpose` (haiku): lists `Bash`, `Edit`, `Write`, `Skill`, `Agent`, `advisor` and every `mcp__plugin_ironclaude_*` tool.
- `worker/agents/reviewer.md:4` is `tools: Read, Grep, Glob`, and every listed restriction held. `advisor` is Claude Code's built-in, parameter-free, report-only consultation tool, present in every subagent including the control; it has no shell, edit or MCP access. The plan's "exactly Read, Grep, Glob" expectation was stricter than A1's purpose (no gate circumvention). Operator ruling 2026-10-18: "why do we care if advisor is working?!" → PASS, no backlog item.

### Step 3
```
probe=0
cache=/Users/roberthyatt/.codex/models_cache.json
configured_opus=sol
resolved_opus=gpt-6.1-sol
route client=codex model=gpt-6.1-sol
result={"grade": "A", "approved": true, "feedback": "Correct, minimal implementation of addition for operands supporting Python's + operator."}
gpt-6.1-sol gpt-6-sol gpt-5.6-sol
```

### Step 4
The operator ran `/ironclaude provider brain codex` and asked "what unfinished work is sitting idle right now?". The Brain answered in Slack: "No unfinished work is idle: `d1501-r15` is running its R15 GM Core review plan; its approval gate is resolving C8 scoring, contamination isolation, and C6-rubric evidence."
```
752:2026-10-18 17:44:22,862 [ironclaude] INFO: Codex hook-trust warm-up finished ready=True trust_prompt_answered=False
756:2026-10-18 17:44:24,863 [ironclaude] INFO: Brain SDK client started client=codex
drwx------@ 6 roberthyatt  staff  192 Oct 18 17:44 /Users/roberthyatt/.ironclaude/state/brain-gate
/Users/roberthyatt/.ironclaude/state/brain-gate/f083d3f2e90b45f6b82739fe738f8f28/hooks-live
/Users/roberthyatt/.codex/sessions/2026/10/18/rollout-2026-10-18T17-44-23-01a15166-f1db-79c0-822a-cc53cd893c1f.jsonl
```
No `Brain SDK client not started client=codex`, `Codex Brain hooks are not running`, `spawn failed: Codex model family` or `servers outside the Brain set` line; the old-root `find` printed nothing. This closes the 2026-10-10 "Commander non-functional with Codex" entry.

### Step 5
The plan's command printed `rollouts=1`, then `sh: rg: command not found` and `rg_rc=127`. In this harness `rg` is a zsh shell function from the Claude Code shell snapshot, not a binary, so `sh -c` cannot find it. The scan therefore did not run; this was not a product failure. Substitute, same patterns on the one new rollout:
```
322                     (lines in the rollout)
grep_rc=1               (no 'professional mode is unset' or 'access unavailable' line)
44                      (positive control: 'd1501-r15' occurrences, the Brain's real work)
```
Verdict PASS. Plan defect for future plans: do not call `rg` inside `sh -c` in this harness.

### Step 6
- **1b PASS.** `send_to_worker` completed at 17:48:57 (`Codex Brain tool event server=orchestrator tool=send_to_worker status=completed`); fast-model calls from 17:49:12 on are `fast model answered via haiku` (21 such lines after the marker, none via another model).
- **2 UNVERIFIED-LIVE: no daemon.log line for evaluate_worker_health; searched daemon.log and the Brain rollout.** `evaluate_worker_health` completed at 18:10:57 with `error_category=None`; the Brain replied "`d1501-r15` is healthy: active design work, no error loop or stall"; the Codex rollout names `evaluate_worker_health` 9 times. The call is `_call_local_grader(..., tier="sonnet")` (`commander/src/ironclaude/orchestrator_mcp.py:6409`), which reaches the Sonnet-class fast model only when the local box fails, so no `fast model answered via sonnet|terra` line is expected while the box answers (not confirmed).
- **3 PASS.** Slack command at 18:11:30 "spawn a claude-opus worker to estimate how much time is left on r15"; `spawn_worker` completed at 18:13:12; the new pane log `/private/tmp/ic-logs/ic-d1501-r15-eta.log` shows `Opus 5.5` and `/advisor^[[12G^[[39mopus`. The TUI draws `/advisor opus` with a cursor move between the words, so the plan's literal `rg -F '/advisor opus'` listed only the older `ic-d1501-r12-reviewer.log`.

### Step 7
Defects 2, 5 and 9 (completeness findings R4b table and operator items) are pre-v1.2.0 bugs that v1.2.0 does not change; the 2026-10-17 scratch evidence stands (2 LIVE in scratch; 5 does not affect the operator's `danger-full-access` config; 9 LIVE in skill text). The operator chose "Skip all three (Recommended)": all three UNVERIFIED-LIVE, kept on the backlog. An empty scratch repository `/private/tmp/claude-502/taf6/scratch-d2` was created for Defect 2 and not used; the guard blocks commits during execution, so it has no initial commit.

### Operator ruling on substitute evidence
The plan's commands for Step 5 (`rg` inside `sh -c`) and Step 6 item 3 (literal `rg -F '/advisor opus'` against a TUI log) failed for harness reasons. PASS was first recorded on substitute evidence without stopping, contrary to design R4; task code review graded that C. The operator ruled 2026-10-18: "Accept both as PASS (Recommended)". Both stand as PASS on the evidence above.

### Backlog decision recorded
The security review's parser-gap findings (git option abbreviations, the Codex `execCommandApproval` argv join and re-split, the `git branch` denylist) stay out of v1.2.0 by operator decision ("Backlog to run_git tool (Recommended)") and are recorded as test cases in memory `project_backlog_brain_run_git_tool.md`.
