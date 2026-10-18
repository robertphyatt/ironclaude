# Claude and Codex Tool Audit per IronClaude Role Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Measure every tool and feature that each IronClaude role gets on Claude and on Codex, and classify each one per role as needed, harmless, conflicting with IronClaude, or unknown. Compare the two clients and rank the recommended changes.

**Requirements:** `docs/plans/2026-10-17-tool-audit-design.md` (A1-A7).

**Architecture:**
- Discover every launch site with a broad search.
- Measure the one-shot roles by running their real argv:
  - Claude roles: the `init` event's tool list plus a self-report, because `init` does not list the server-side `advisor` tool.
  - Codex roles: `codex features list` with the role's own flags plus a self-report.
- Assess the Brain, workers and the SDK-based summarizer from source, since launching them could have side effects.

**Tech Stack:** Claude CLI, Codex CLI 0.163.0-alpha.10.2, Python (the Commander venv), ripgrep.

Loop: light.

**Execution invariants:**
- Shell state does not persist between steps, so every step uses absolute paths.
- Evidence goes under `/private/tmp/claude-502/audit/`, one file per run.
- No `2>/dev/null` on evidence.
- Never write `~/.codex/config.toml`, and never edit source.
- Never start a live Brain, an interactive worker, or the episodic summarizer SDK.
- Measure the Codex broker by running its literal argv directly, never through the `run_codex_advisor_review` MCP tool, which writes broker state.
- Build every one-shot argv from the shipped functions or the literal source argv. Never retype flags.
- Expected values are recorded results; measuring them is the deliverable.
- `docs/` is gitignored, so docs are staged with `git add -f`.
- Shared probe inputs:
  - **Self-report prompt:** `List the exact names of every tool you can call in this session, comma-separated, in the answer field. If none, answer NONE.`
  - **One-field schema:** `{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}`

**Roles:**
- **Measured one-shot:**
  1. Claude grader (`_claude_grader_argv`)
  2. Codex grader (`_codex_grader_argv`)
  3. Claude Commander fast child (`fast_model.claude_fast_argv`, Haiku tier; the Sonnet tier uses the same argv with another model)
  4. Codex Commander fast child (`fast_model.codex_fast_argv`, Luna tier; Terra is the same argv)
  5. Claude hook fast child (literal argv in `worker/hooks/plan-validator.sh`)
  6. Codex hook fast child (literal argv in the same file)
  7. Codex advisor broker (literal argv at `advisor-review.ts:610-624`)
  8. Fable subagent runner (`run_fable_subagent.build_argv`)
  9. Positive controls: `claude -p --no-session-persistence --output-format json`, and `codex exec --json --ephemeral --skip-git-repo-check -s read-only -m gpt-5.6-luna -`
- **Assessed from source, not measured:**
  10. Claude Brain (`brain_client.py`, permission mode)
  11. Codex Brain (app-server)
  12. Interactive Claude workers (Opus and Fable at `config.py:241`; Sonnet at `main.py:4068` and `orchestrator_mcp.py:180`, `673-678` and `700-701`)
  13. Ollama-backed Claude workers (`orchestrator_mcp.py:689`, `4990` and `5917`; `main.py:4045`)
  14. Interactive Codex worker (`orchestrator_mcp.py:590` and `789`)
  15. Worker resume for both clients (`orchestrator_mcp.py:6821-6837`)
  16. Handoff bootstrap for both clients (`worker_handoff.py:1349-1358`)
  17. Handoff resume for both clients (`worker_handoff.py:1501-1509`)
  18. Episodic summarizer, fresh and resume branches (`summarizer.ts:312-329`; record the SDK version from `worker/mcp-servers/episodic-memory/node_modules/@anthropic-ai/claude-agent-sdk/package.json`)
  19. Claude advisor-fallback `Agent` subagent (inherits its parent's tools; documentation only)
  20. The server-side `advisor` tool, folded into each Claude role's matrix from the self-reports
- **Out of scope** (HTTP-only, no agent tool surface; listed so the inventory is complete): the local-box/OpenAI arm of the hook fast call, the Ollama/OpenAI arm of the summarizer, and the shadow grader.

---

## Task 1: Audit and findings

No tests required: investigation only, with no source change.

**Files:** Create `docs/plans/2026-10-17-tool-audit-findings.md`. Stage the design, this plan, its `.plan.json` and the findings.

**Step 1: Discover launch sites (A1)**

```bash
mkdir -p /private/tmp/claude-502/audit && rg -n --no-heading -e 'claude-agent-sdk' -e 'claude_agent_sdk' -e 'ClaudeAgentOptions' -e 'query\(\{' -e 'permission_mode' -e 'app-server' -e 'codex exec' -e '"exec"' -e "'exec'" -e 'exec \{' -e 'exec claude' -e 'claude -p' -e '"-p"' -e "'-p'" -e '--print' -e '--dangerously-' -e '--permission-mode' -e '--sandbox' -e '"-s"' -e "'-s'" -e '--tools' -e '--strict-mcp-config' -e '--restricted' -e 'spawn\(' -e 'spawn_session' -e 'create_subprocess_exec' -e 'subprocess\.(run|Popen)' -e '_fast_argv' -e '_grader_argv' -e 'build_argv' -e '_worker_command' -e 'make_opus_command' /Users/roberthyatt/Code/ironclaude/commander/src /Users/roberthyatt/Code/ironclaude/worker/hooks /Users/roberthyatt/Code/ironclaude/worker/mcp-servers /Users/roberthyatt/Code/ironclaude/worker/skills --glob '!**/node_modules/**' --glob '!**/dist/**' --glob '!**/__tests__/**' --glob '!**/tests/**' --glob '!**/__pycache__/**' --glob '!*.md' > /private/tmp/claude-502/audit/launch-sites.txt 2>&1; echo "rg=$?"; wc -l /private/tmp/claude-502/audit/launch-sites.txt; rg -l -e claude -e codex /Users/roberthyatt/Code/ironclaude/worker/skills --glob '*.{py,sh,ts,js,mjs}' > /private/tmp/claude-502/audit/skill-scripts.txt 2>&1; echo "rgs=$?"; cat /private/tmp/claude-502/audit/skill-scripts.txt
```

Expected: record `rg=`, the line count and `rgs=`. Triage every hit, which will be noisy. Each of roles 1-19 must map to at least one hit with file and line, and the inventory records the exact argv or options read from the code around it. A role with no hit is a discovery failure: record it and widen the patterns, but never drop the role. Add any further launch site found as a new role.

**Step 2: Codex surface (A3)**

```bash
codex --version > /private/tmp/claude-502/audit/codex-version.txt 2>&1; echo "v=$?"; codex features list > /private/tmp/claude-502/audit/codex-features.txt 2>&1; echo "f=$?"; codex --help > /private/tmp/claude-502/audit/codex-help.txt 2>&1; echo "h=$?"; codex exec --help > /private/tmp/claude-502/audit/codex-exec-help.txt 2>&1; echo "eh=$?"; rg -n -i -e tool -e debug /private/tmp/claude-502/audit/codex-help.txt /private/tmp/claude-502/audit/codex-exec-help.txt
```

Expected: record each rc. Label the bare features list "user-config state". Record any subcommand that lists tools; if one exists, run it per Codex role in Step 4.

**Step 3: Claude one-shot measurements (A2): roles 1, 3, 5, 8 and the Claude control**

Run each role twice, from cwd `/tmp`, or the grader's `_grader_home`, with the role's own env (`tools._grader_env("low")` for the grader):
- **(a) `init` run:** the prompt `Reply with ok.`, with the one-field schema wherever the argv takes `--json-schema` (fast child and hook). Save to `claude-<role>-init.json`. Extract the system init event with the Commander venv Python: for the JSON array output, find the element with `type=="system"` and `subtype=="init"`. For the Fable runner's `stream-json` output, read each line as one event and find the same element. Record `tools`, `mcp_servers` and `model` verbatim; if no init event exists, record `init: MISSING` as a measurement failure.
- **(b) self-report run:** the self-report prompt with the one-field schema. Save to `claude-<role>-self.json` and record the `answer` verbatim, labelled as self-report.

The grader argv comes from `OrchestratorTools._claude_grader_argv`, built with the config pattern in `/private/tmp/claude-502/cgh/grader_probe.py`. The fast child uses `fast_model.claude_fast_argv(path, model, schema)`. The hook argv is the literal one in `plan-validator.sh`. The Fable runner uses `run_fable_subagent.build_argv`, with the prompt on stdin; if Fable is unavailable, record a measurement failure and never substitute another model. The Claude control is `claude -p --no-session-persistence --output-format json "<prompt>"`.

Expected: per role, the `init` list and the self-report side by side. Where they differ, classify the role by its self-report and note the difference as an evidence-source caveat.

**Step 4: Codex one-shot measurements (A3): roles 2, 4, 6, 7 and the Codex control**

- **(a) Effective features:** for each role, run `codex <that role's -c feature overrides, plus --ignore-user-config/--ignore-rules where the role uses them> features list` into `codex-features-<role>.txt`. If the CLI rejects a flag before `features`, record the rc and the caveat "effective state not measurable; derived from argv".
- **(b) Self-report:** run the role's real argv with the self-report prompt on stdin and the one-field schema. The broker gets no schema, because its shipped argv has none; run it in a fresh `mktemp -d`. Save the `--json` stdout to `codex-<role>.jsonl` and record the final `agent_message`, labelled as self-report.

The grader argv comes from `_codex_grader_argv`, the fast child from `fast_model.codex_fast_argv`, the hook argv is the literal one in `plan-validator.sh`, and the broker is the literal argv at `advisor-review.ts:610-624` with the reviewer model it resolves (`gpt-6-sol` or the newest Sol). The control is `codex exec --json --ephemeral --skip-git-repo-check -s read-only -m gpt-5.6-luna -`.

Expected: for each role, its effective features list and its self-reported tools.

**Step 5: Assessed roles (A4): roles 10-19**

For each role, write its tool surface from the source: allowed and disallowed tools, permission mode, sandbox, MCP servers, hooks, features and setting sources. Mark it "not measured", with file and line.

**Step 6: Findings (A5-A7)**

Write `docs/plans/2026-10-17-tool-audit-findings.md` with:
1. The role inventory: roles 1-20 plus the out-of-scope list, with argv and file:line.
2. The evidence file paths under `/private/tmp/claude-502/audit/`, with key lines inline.
3. One matrix per role: each tool or feature from Steps 2-5, classified as needed, harmless, conflicts with IronClaude, or unknown, with a one-line reason. Conflict categories are file read/write, shell, network egress, spawning agents or workers, worktrees, scheduling, plugin/MCP/hook loading, and persistent memory.
4. A Claude-against-Codex comparison per role, listing every asymmetry, including the `advisor` tool, reasoning effort, the 10 Codex features and batch grading.
5. Ranked recommended changes, each with the role, the change, the evidence and the risk, for the operator to approve.

**Step 7: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-tool-audit-design.md docs/plans/2026-10-17-tool-audit.md docs/plans/2026-10-17-tool-audit.plan.json docs/plans/2026-10-17-tool-audit-findings.md; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status
```

Expected: four `A` lines naming exactly these paths.
