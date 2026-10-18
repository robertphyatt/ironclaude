# v1.2.0 Completeness Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Finish v1.2.0 before tagging. Claude reviewers get a locked read-only agent. IronClaude pins no Codex model: Codex defaults become family words, resolved where Codex runs, and an explicit slug wins. The Brain gate root moves under `~/.ironclaude/state` and fails closed. The commit and worktree defects are verified against 1.2.0. A failed local box is skipped for a configurable backoff. The drift check's cross-session ledger rule counts only another live session's in-progress rows. Commander-on-Codex and the deployed changes are verified live.

**Requirements:** `docs/plans/2026-10-17-v120-completeness-design.md` (R0-R6, R4b "Commit and worktree verification (Part E)", R4c "Local-box backoff (Part F)" with its "Amendment (2026-10-17): only box failures count" and that amendment's "The hook's budget counts" bullet (2026-10-18), and "Amendment (2026-10-18): one skip rule", R4d "Drift rule (b) counts only in-progress rows (Part G)" and "Corrections from plan grounding"). This is both the requirements file and the design file.

**Architecture:**
- **Locked reviewer (R1).** `worker/agents/reviewer.md` is a report-only plugin agent with `tools: Read, Grep, Glob` and no `model` pin. Claude dispatches it as `subagent_type="ironclaude:reviewer"` in three places: the advisor fallback, the plan review and the end review. It has no Bash, so the orchestrator puts the diff, test output and other command evidence in the prompt. Execution subagents stay `general-purpose`; that gap is on the backlog.
- **Codex model rule (R2).**
  - **Defaults.** `config.DEFAULTS` Codex models become the family words `luna`, `terra`, `sol` and `astra`, mirroring Claude's `haiku`, `sonnet`, `opus` and `fable` aliases.
  - **Validation.** `provider_config` accepts the tier's family word or a `gpt-<version>-<word>` slug whose word matches the tier.
  - **Resolution.** `provider_config.resolve_codex_model(value, cache_text, cache_location)` returns an explicit slug as-is. A family word resolves to the newest `gpt-<version>-<word>` in a `models_cache.json` text, by numeric version. If none is listed, it raises `CodexModelUnavailable`, whose message names the family and the cache location.
  - **Local and remote.** `resolve_local_codex_model` reads this Mac's `$CODEX_HOME/models_cache.json` (default `~/.codex`). `ProviderConfig.model_for` and `advisor_model_for` take a required keyword `host` and an optional `codex_cache` reader. On `host == "local"` they read the local cache. On a remote host they read the cache through `codex_cache(host)`, and with no reader the family is unavailable.
  - **Routing.** `ProviderRouter.resolve` passes `codex_cache` through. When it catches `CodexModelUnavailable` it logs a warning and tries the next client or tier, so routing falls to the next client.
  - **Remote workers.** `_resolve_remote_worker_client` reads that box's own cache over `SSHConnectionManager.run_argv` with `REMOTE_CODEX_CACHE_ARGV = ("sh", "-c", 'cat "${CODEX_HOME:-$HOME/.codex}/models_cache.json"')`, prefixed with `env CODEX_HOME=<value>` when the machine's `env` sets `CODEX_HOME`. `run_argv` shell-quotes each argument, so only `sh -c` expands the path on the remote host.
  - **Other callers.** The Codex Brain, the fast children and the Codex worker resume use `resolve_local_codex_model`. A Brain whose family is not installed fails start visibly through the existing `spawn failed:` path. A fast-model call skips Codex. A resume returns an error before it spawns anything.
  - **Hook and broker.** The hook's `_newest_codex_model` prints nothing, logs the family and cache path to stderr, and returns 1, so the hook fails open. The broker returns a new `failure()` reason, `codex-reviewer-model-unavailable`; no existing reason fits.
  - **Pinned slugs removed** from `config.py`, `provider_config.EXPECTED_MODELS`, `codex_brain_client.py`, `fast_model.py`, `orchestrator_mcp.py`, `plan-validator.sh`, `advisor-review.ts` and `dist/index.js`. A Python guard keeps them out, and docs that cited default slugs are updated.
  - **Tests.** The conftest `CODEX_HOME` holds a fixture `models_cache.json` (`gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol`, `gpt-6-astra`). That way the family-word defaults resolve deterministically and never to a developer's installed models.
- **Brain gate root (R3).**
  - **Location.** `paths.brain_gate_root()` returns `~/.ironclaude/state/brain-gate`. It is resolved at call time, as the paths seam requires. `paths.ensure_brain_gate_root()` creates the root and sets mode 0700.
  - **Clients.** Both Brain clients call it at start and pass the root as `IRONCLAUDE_BRAIN_GATE_ROOT` beside `IRONCLAUDE_BRAIN_GATE_SESSION`.
  - **Hooks.** `brain-gated-actions.sh` and `brain-hooks-canary.sh` read that variable (default `$HOME/.ironclaude/state/brain-gate`) and share one `gate_root_problem` check. The check refuses a symlink, a non-directory, a root this user does not own, and any group or other permission bit. On refusal the gate blocks and the canary writes nothing.
  - **Unchanged.** `hooks.json` is not touched, and the rest of `/tmp/ic` stays where it is.
- **Commit and worktree verification (R4b).** This part changes no code. It reproduces 9 recorded defects against the 1.2.0 code in throwaway repositories under `/private/tmp/claude-502/taf2/r4b/`, driving the real `state-activator.sh` hook and the real workspace-manager `dist` bundle. Defects that need a real Codex session, the sandbox or Slack get operator-assisted steps or an `UNVERIFIED-LIVE` trace. Each defect is recorded as LIVE, FIXED or UNVERIFIED, with evidence.
- **Local-box backoff (R4c, Part F).**
  - **Rule.** A box failure marks the box down: a connect failure, an HTTP error, an empty answer, invalid JSON, a read timeout at the box's own configured read timeout, or (in the Commander) any other local `infrastructure_error`. The hook's own budget timeout (`curl --max-time` = `hook_validation_budget_seconds`, default 8) counts as a box failure, so the hook is unchanged by the amendment. The only exemption is in the Commander: a read timeout under a per-call `read_timeout` shorter than the Commander client's configured read timeout (such as the fast lane's `fast_lane_grade_timeout_seconds`, default 5) writes no marker and clears none (R4c amendment and its "The hook's budget counts" bullet, Task 14). While a live marker matches the configured endpoint, the hook's `call_validation_llm` and the Commander's `LocalGrader.grade` skip the box and go straight to the Haiku-class fast model. After expiry, the next call tries the box again: a success clears the marker and a failure renews it.
  - **Marker.** There is one marker, `local-box-backoff.json`, which holds `{"endpoint", "until"}` (epoch seconds). It lives in `${IRONCLAUDE_LOCAL_BACKOFF_DIR:-$HOME/.ironclaude/state/local-backoff}`, a dedicated 0700 directory that is owner-checked by the same check as the R3 gate root, so an existing group-readable `~/.ironclaude/state` cannot disable it.
  - **Config.** `local_backoff_minutes` is resolved like `_resolve_budgets`, block then top: `.<backend>.local_backoff_minutes // .local_backoff_minutes // 15`. `0` disables it. The plan never edits the operator's hooks config.
  - **One source of truth.** The Commander gets `ironclaude/local_backoff.py`, and `LocalGrader.grade` consults it before the client call. The in-process transport breaker in `ollama_client._BREAKERS` stays, because the ollama MCP, VRAM probes and the shadow grader also use it.
  - **One skip rule (R4c amendment 2026-10-18, Task 15).** In the fast-model chain the shared marker is the only skip decision. `OllamaClient` and `OpenAiClient` take a constructor keyword `use_breaker` (default `True`), and `make_client` passes it through. `LocalGrader._build_client` builds its normal and fast-lane clients with `use_breaker=False`, so their `_attempt` skips the `_BREAKERS.allow(url)` gate but still records every outcome (success, failure, slow). At `local_backoff_minutes: 0` the box is tried on every call, and no other caller's failures make the grader skip it. Every other caller keeps the default and the breaker. The hooks have no breaker and are unchanged.
  - **Parity tests.** Python tests compare the hook's default, directory, marker name and directory check against the module and against `gate_root_problem`, and they write the marker from one side and read it from the other.
  - **Out of scope.** The shadow grader, which has no fast-model step, and `list_claude_sessions` summarization keep their existing local-box handling.
- **Drift rule (b) (R4d, Part G).** In `worker/hooks/execution-drift-check.sh` `_drift_foreign_paths`, rule (b) excludes a path that another session's same-root ledger lists only while that ledger's `wave_tasks` row is `in_progress` and its session is executing or reviewing. The liveness query gains `AND w.status='in_progress'`, so a completed row's ledger no longer excuses this session's own write. The ledgers are still never pruned.
- **Live verification (R0 A1, R4, R6).** These checks need the deployed plugin, so they run in the final task, after the operator commits and deploys.

**Tech Stack:** Python and pytest (Commander); bash and jq (hooks); TypeScript, vitest and esbuild (state-manager); the Claude Code and Codex CLIs.

**Execution invariants:**
- **Shell and paths.**
  - Shell state does not persist between steps, so every step uses absolute paths.
  - The repo root is `/Users/roberthyatt/Code/ironclaude`; git runs as `git -C /Users/roberthyatt/Code/ironclaude`. The Bash working directory at the start of each step is that repo root; commands still use absolute paths or an explicit `cd`.
  - The shell is zsh with `nomatch`, so globs are quoted wherever they must reach a program unexpanded.
  - Foreground `sleep` is blocked; to wait, use a Monitor until-loop.
- **Python.** The venv interpreter is `/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python`. Commander tests run as `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest ...`. A full Commander suite run may take several minutes, so give that Bash call `timeout: 600000`.
- **Hook tests.**
  - `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/<file>.sh`;
  - `bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh`;
  - `bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh`.
- **State-manager.**
  - Commands run with cwd `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager`: `npm test`, `npx vitest run <file>` and `npx tsc --noEmit`.
  - `dist/index.js` is the runtime bundle. It is rebuilt only by `npm run bundle` (esbuild), never by `npm run build`. It is tracked although `dist/` is gitignored, so `git add` works on it without `-f`.
- **Logs and staging.**
  - Each run writes its own log under `/private/tmp/claude-502/taf2/`. Evidence commands never use `2>/dev/null`.
  - `docs/` is gitignored, so `docs/plans` files are staged with `git add -f`.
  - `worker/.codex-plugin/plugin.json` is modified and unstaged at plan time and is not part of this plan. Never stage it.
- **Prohibited actions.**
  - Never write `~/.codex/config.toml`. Never commit, tag or push; the operator does those.
  - Never call workspace-manager `allocate` or `activate` on a real repository. Reproductions use throwaway repositories under `/private/tmp/claude-502/taf2/r4b/` only, and never touch the operator's checkouts.
- **Expectations and stops.**
  - Every RED expectation names exactly the tests that fail, derived by tracing the current tests. If a RED run differs, STOP and report; do not adjust the tests to match.
  - Any GREEN run that shows a failure outside the named baseline set is a real STOP.
- **State-manager load rule.** If the ONLY extra vitest failure is `runCodexAdvisorReview > kills a real SIGTERM-resistant parent and grandchild process group`:
  1. Copy the log to `<log>.loaded` and append `uptime`.
  2. Wait with a Monitor until-loop on `uptime` until the 1-minute load is below 8, for at most 30 minutes. If it never drops, append the final `uptime` and STOP for the operator.
  3. Rerun that step's command once and judge on the rerun. Never kill other processes. Any other extra failure is a real STOP.
- **Line numbers and anchors.** Cited line numbers describe each file before the task's own edits. When an earlier edit in the same step shifts lines (for example, `provider_config.py` lines 21-71 before the error text at 173-177, or `advisor-review.ts` lines 21-22 before 227-255 and 617-619), match on the quoted anchor text, not on the number.
- **Orchestrator-only steps.** Steps marked "Orchestrator step" are run by the main session. Subagents cannot dispatch agents, so these steps have no shell command.

---

## Task 1: Baseline measurements (no source change)

No tests required: measurements only, no source change.

**Files:** Create `docs/plans/2026-10-17-v120-completeness-findings.md`.

**Step 1: Scratch directory**

```bash
mkdir -p /private/tmp/claude-502/taf2 && ls -ld /private/tmp/claude-502/taf2
```

Expected: one `drwx` line for `/private/tmp/claude-502/taf2`.

**Step 2: A2, subagent session binding (orchestrator step)**

1. The orchestrator calls `mcp__plugin_ironclaude_state-manager__get_resume_state` itself and records the `session_id` it returns as `parent_session_id`.
2. It dispatches one `Agent` with `subagent_type="general-purpose"` and `model="haiku"`, using this prompt verbatim:

   `Call the MCP tool mcp__plugin_ironclaude_state-manager__get_resume_state exactly once with no arguments. Call no other tool. Reply with only the value of its session_id field, verbatim.`

3. It records the reply as `subagent_session_id`.

Never probe with a write call.

Expected: both values are recorded. Write `A2: SAME` if they are equal and `A2: DIFFERENT` otherwise; either result is recorded, not judged. The precedent from the 2026-10-16 audit is SAME.

**Step 3: Commander suite baseline**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/taf2/t1-suite.log 2>&1; echo "suite=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t1-suite.log; tail -n 1 /private/tmp/claude-502/taf2/t1-suite.log
```

Expected: record `suite=` and the summary line. Every FAILED or ERROR line is the Commander baseline failing set; it is empty if `suite=0`.

**Step 4: Hook suites baseline**

```bash
for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh; do bash "$t" > "/private/tmp/claude-502/taf2/t1-hooks-$(basename "$t").log" 2>&1; echo "$(basename "$t") rc=$?"; done
```

Expected: 46 lines, one per file: the 44 files under `worker/hooks/tests/`, then `test-brain-gated-actions.sh` and `test-brain-hooks-canary.sh`. Record every `rc=` that is not 0; those files are the hooks baseline failing set.

**Step 5: State-manager suite and typecheck baseline**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf2/t1-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf2/t1-npm.log; npx tsc --noEmit > /private/tmp/claude-502/taf2/t1-tsc.log 2>&1; echo "tsc=$?"; cat /private/tmp/claude-502/taf2/t1-tsc.log
```

Expected: record `npm=` and the summary lines. Every FAIL test name is the state-manager baseline failing set; it is empty if `npm=0`. Record `tsc=` and every `error TS` line as the typecheck baseline.

**Step 6: Record the baseline and stage**

Write `docs/plans/2026-10-17-v120-completeness-findings.md` with a section `## Baseline (before change)`. It holds:
- the Step 2 values and the A2 verdict;
- the Step 3, 4 and 5 outputs and failing sets;
- a note that A1 (the reviewer's tool list) runs in Task 13, because a new plugin agent is dispatchable only after the plugin is redeployed to the Claude cache.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-v120-completeness-findings.md
```

Expected: no output.

---

## Task 2: Locked reviewer agent (R1) (depends on 1)

**Files:**
- Create `worker/agents/reviewer.md`.
- Modify `worker/.claude-plugin/plugin.json`. Its `agents` array lists the plugin's agents explicitly (today only `./agents/search-conversations.md`), so the new agent must be added there to be dispatchable.
- Modify `worker/skills/advisor-fallback/SKILL.md`, lines 37-41: the "If you are a Claude session" dispatch paragraph.
- Modify `worker/skills/executing-plans/SKILL.md`:
  - lines 271-273, the plan-review Claude Code bullet;
  - lines 861-866, the end-review dispatch.
- Create `commander/tests/test_reviewer_agent.py`.
- Modify `commander/tests/test_advisor_fallback_directive.py`, lines 131-135.

**Step 1: Tests (RED)**

Create `commander/tests/test_reviewer_agent.py`:

```python
"""Design 2026-10-17 v120-completeness R1: one locked, report-only reviewer agent replaces the
Explore and general-purpose reviewer dispatches."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "worker" / "agents" / "reviewer.md"
ADVISOR = REPO / "worker" / "skills" / "advisor-fallback" / "SKILL.md"
EXECUTING = REPO / "worker" / "skills" / "executing-plans" / "SKILL.md"
DISPATCH = 'subagent_type="ironclaude:reviewer"'


def _frontmatter(text):
    assert text.startswith("---\n"), "reviewer.md has no frontmatter"
    block = text.split("---\n", 2)[1]
    return {key.strip(): value.strip() for key, value in
            (line.split(":", 1) for line in block.strip().splitlines())}


def test_reviewer_agent_is_read_only_and_unpinned():
    text = AGENT.read_text()
    fields = _frontmatter(text)
    assert fields["tools"] == "Read, Grep, Glob"
    assert "model" not in fields  # callers pass model=
    assert fields["description"]
    assert "report-only" in text.lower()
    import json
    manifest = json.loads((REPO / "worker" / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["agents"] == ["./agents/search-conversations.md", "./agents/reviewer.md"]


def test_reviewer_dispatches_use_the_locked_agent():
    advisor = ADVISOR.read_text()
    assert DISPATCH in advisor
    assert 'subagent_type="Explore"' not in advisor
    text = EXECUTING.read_text()
    plan_review = text[text.index("- **Claude Code:** use a fresh `Agent`"):
                       text.index("- **Codex:** call `run_codex_advisor_review`")]
    assert DISPATCH in plan_review
    end_review = text[text.index("**Run the review (high-blast-radius + confirmed):**"):
                      text.index("**Step 8: Final summary**")]
    assert DISPATCH in end_review
    assert "`git diff --staged` output" in end_review and "inline" in end_review
    # Execution subagents stay general-purpose (backlog: the parent-stage hijack), so exactly one remains.
    assert text.count('subagent_type="general-purpose"') == 1
    assert 'Dispatch one subagent per task (Task tool with subagent_type="general-purpose")' in text
```

In `commander/tests/test_advisor_fallback_directive.py`, replace lines 131-135 (`test_claude_advisor_reviewer_is_a_read_only_explore_agent`) with:

```python
def test_claude_advisor_reviewer_is_the_locked_reviewer_agent():
    """Design 2026-10-17 v120-completeness R1: the report-only Claude reviewer is ironclaude:reviewer."""
    text = _read("worker/skills/advisor-fallback/SKILL.md")
    assert 'subagent_type="ironclaude:reviewer"' in text
    assert 'subagent_type="Explore"' not in text
    assert "general-purpose" not in text
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_reviewer_agent.py tests/test_advisor_fallback_directive.py tests/test_executing_plans_skill.py -q > /private/tmp/claude-502/taf2/t2-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t2-red.log; tail -n 1 /private/tmp/claude-502/taf2/t2-red.log
```

Expected: `red=1`. FAILED is exactly these 3:
- `test_reviewer_agent_is_read_only_and_unpinned` (FileNotFoundError)
- `test_reviewer_dispatches_use_the_locked_agent`
- `test_claude_advisor_reviewer_is_the_locked_reviewer_agent`

**Step 2: Agent and skill text (GREEN)**

Create `worker/agents/reviewer.md`:

```markdown
---
description: IronClaude's locked, report-only adversarial reviewer for plan reviews, end reviews and the advisor fallback. It reads the repository with Read, Grep and Glob only; it cannot run commands, edit files, load skills or call MCP tools. Callers pass model= and put the diff, test output and other command evidence in the prompt.
capabilities: ["adversarial-review", "plan-review", "code-review"]
tools: Read, Grep, Glob
---

# IronClaude Reviewer

You are a fresh, blind, report-only adversarial reviewer. You identify problems with evidence. You
never rewrite the work, and you never claim to have run a command.

## Your tools

You have Read, Grep and Glob only. You cannot run git, tests or any shell command. Every diff, test
result and command output you need is in your prompt. If evidence you need is missing, name exactly
what is missing instead of guessing.

## Method (ironclaude:adversarial-review)

1. **Orientation.** Read the task, the decision under review, the operator constraints and the
   specific questions in your prompt.
2. **Deep read.** Read the changed files and the code they touch with Read, Grep and Glob.
3. **Verify every finding.** Back each finding with a file:line you read, or with evidence quoted from
   the prompt. Drop any finding you cannot verify.
4. **Report.** Classify each verified finding by severity, MATERIAL or OBSERVATION, and give its
   evidence and the smallest correct fix. Give the verdict format your prompt asks for.

## Rules

- Report-only: do not propose to edit files yourself, and do not ask to be given more tools.
- Evaluate the work as it stands. Ignore author rationale unless your prompt says it is in scope.
- Weigh operator directives and settled design decisions above derived documents.
```

In `worker/.claude-plugin/plugin.json`, replace the `agents` array with:

```json
  "agents": [
    "./agents/search-conversations.md",
    "./agents/reviewer.md"
  ]
```

In `worker/skills/advisor-fallback/SKILL.md`, replace lines 37-38:

```text
Dispatch a fresh, blind subagent with the `Agent` tool (`subagent_type="Explore"`, a read-only agent type with no Edit or Write tool,
`model=opus`, or `model=sonnet` from a Haiku session — see **Claude reviewer** below), giving it the task, the change/decision, the evidence, and the specific questions
```

with:

```text
Dispatch a fresh, blind subagent with the `Agent` tool (`subagent_type="ironclaude:reviewer"`, IronClaude's locked report-only
reviewer with Read, Grep and Glob only — no Bash, Edit, Write, Skill or MCP tools — and `model=opus`, or `model=sonnet` from a
Haiku session — see **Claude reviewer** below), giving it the task, the change/decision, the evidence, and the specific questions
```

Then replace the next line's sentence start `to pressure-test. Have it follow` with `to pressure-test. The reviewer cannot run commands, so put the diff, test output and any other command evidence in the prompt. Have it follow`. The rest of that line stays unchanged.

In `worker/skills/executing-plans/SKILL.md`, replace lines 271-273:

```text
   - **Claude Code:** use a fresh `Agent`
     (`subagent_type="general-purpose"`, `model=<resolved model>`). Put the complete
     provenance packet inline and provide current artifact/source paths.
```

with:

```text
   - **Claude Code:** use a fresh `Agent`
     (`subagent_type="ironclaude:reviewer"`, `model=<resolved model>`). The reviewer has Read,
     Grep and Glob only, with no Bash, Skill or MCP tools. Put the complete provenance packet
     inline, plus any command output it needs, and provide current artifact/source paths.
```

Replace lines 861-866, from `**Run the review (high-blast-radius + confirmed):**` through `current code as-is — NO git-blame, NO prior-review or issue-tracker context, NO author rationale. Then:`, with:

```text
**Run the review (high-blast-radius + confirmed):** dispatch a fresh, blind, **one-tier-up** reviewer
(Agent tool, `subagent_type="ironclaude:reviewer"`, `model=<one tier above your current model, subject to the Top-tier rule>` per the
Step 1.5 tier-up resolution incl. the Fable-availability fallback and the Top-tier rule) over the staged diff. The reviewer has
Read, Grep and Glob only and cannot run git, so paste the full `git diff --staged` output and the relevant test output inline in
its prompt. It follows the `ironclaude:adversarial-review` approach: orientation → deep read → **verify every finding with grep/read
evidence (drop unverified)** → report severity-classified findings. The reviewer must evaluate the diff /
current code as-is — NO git-blame, NO prior-review or issue-tracker context, NO author rationale. Then:
```

Line 521 (`Dispatch one subagent per task (Task tool with subagent_type="general-purpose")`) does not change.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_reviewer_agent.py tests/test_advisor_fallback_directive.py tests/test_executing_plans_skill.py tests/test_instruction_surface_parity.py -q > /private/tmp/claude-502/taf2/t2-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t2-green.log; tail -n 1 /private/tmp/claude-502/taf2/t2-green.log
```

Expected: `green=0`, 0 failed.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/agents/reviewer.md worker/.claude-plugin/plugin.json worker/skills/advisor-fallback/SKILL.md worker/skills/executing-plans/SKILL.md commander/tests/test_reviewer_agent.py commander/tests/test_advisor_fallback_directive.py
```

Expected: no output.

---

## Task 3: Required `host` keyword and the bundle parity test (R2a) (depends on 2)

**Files:**
- Modify `commander/src/ironclaude/provider_config.py`, lines 100 and 119-121: the `model_for` and `advisor_model_for` signatures.
- Modify `commander/tests/test_provider_config.py`: add `host="local"` to existing calls, and append one test.
- Modify `commander/tests/test_fast_model.py` (append).

**Step 1: Tests (RED)**

In `commander/tests/test_provider_config.py`, add `host="local"` as the last argument of every `model_for(` and `advisor_model_for(` call that has no `host=`. These are the calls on lines 29, 50, 51, 52, 53, 60, 136, 137, 143, 228, 229, 230, 231, 232, 233, 245, 246, 247, 248, 260, 299, 304, 322, 323, 324, 325, 326 and 361. Lines 328-330 already pass `host`. For example, line 29 becomes `assert cfg.model_for("codex", "opus", host="local") == "gpt-5.6-sol"`, and line 232 becomes `assert cfg.advisor_model_for("claude", "claude-sonnet", "opus", host="local") == "company-opus-advisor"`.

Append to `commander/tests/test_provider_config.py`:

```python


def test_model_for_requires_host_keyword(base_config):
    """Design 2026-10-17 v120-completeness R2: a caller that omits host fails instead of silently
    resolving on this Mac."""
    cfg = parse_provider_config(base_config())
    with pytest.raises(TypeError):
        cfg.model_for("codex", "opus")
    with pytest.raises(TypeError):
        cfg.advisor_model_for("codex", "claude-opus", "opus")
    assert cfg.model_for("codex", "opus", host="local") == "gpt-5.6-sol"  # positive control
```

Append to `commander/tests/test_fast_model.py`:

```python


def _bundle_no_tools(bundle_text):
    """The CODEX_NO_TOOLS_ARGS list esbuild wrote into the state-manager runtime bundle, or None."""
    import ast
    import re
    body = re.search(r"^var CODEX_NO_TOOLS_ARGS = \[(.*?)^\];", bundle_text, re.S | re.M)
    return None if body is None else list(ast.literal_eval("[" + body.group(1) + "]"))


def test_codex_no_tools_args_match_bundle():
    """Design 2026-10-17 v120-completeness R2: the bundle the broker actually runs
    (state-manager dist/index.js) carries the same no-tools pairs, in order, and spreads them."""
    import pathlib
    bundle = (pathlib.Path(__file__).resolve().parents[2] / "worker" / "mcp-servers" / "state-manager"
              / "dist" / "index.js").read_text()
    assert _bundle_no_tools(bundle) == list(fast_model.CODEX_NO_TOOLS_ARGS)
    assert "...CODEX_NO_TOOLS_ARGS," in bundle
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_provider_config.py tests/test_fast_model.py -q > /private/tmp/claude-502/taf2/t3-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t3-red.log; tail -n 1 /private/tmp/claude-502/taf2/t3-red.log
```

Expected: `red=1`. FAILED is exactly `test_model_for_requires_host_keyword`, because `host` still defaults to `"local"`. `test_codex_no_tools_args_match_bundle` passes: the current bundle matches. Step 2 proves that it can fail.

**Step 2: Prove the bundle parity test catches drift**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -c 'import sys; sys.path.insert(0, "src"); from tests.test_fast_model import _bundle_no_tools; from ironclaude import fast_model; t = open("/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js").read(); print("real_match=" + str(_bundle_no_tools(t) == list(fast_model.CODEX_NO_TOOLS_ARGS))); print("drifted_match=" + str(_bundle_no_tools(t.replace("\"agents.enabled=false\"", "\"agents.enabled=true\"", 1)) == list(fast_model.CODEX_NO_TOOLS_ARGS)))'
```

Expected: `real_match=True` then `drifted_match=False`.

**Step 3: Required keyword (GREEN)**

In `commander/src/ironclaude/provider_config.py`:
- Line 100 becomes `    def model_for(self, client: str, tier: str, role: str | None = None, *, host: str) -> str:`.
- In `advisor_model_for` (lines 119-121), `*, host: str = "local"` becomes `*, host: str`.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_provider_config.py tests/test_provider_router.py tests/test_fast_model.py tests/test_fast_model_chain.py tests/test_grader_routing.py tests/test_grader_router.py tests/test_worker_handoff.py tests/test_worker_adapter.py tests/test_orchestrator_mcp.py -q > /private/tmp/claude-502/taf2/t3-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t3-green.log; tail -n 1 /private/tmp/claude-502/taf2/t3-green.log
```

Expected: `green=0`, 0 failed. Every production caller already passes `host`: `provider_router.py:82-86` and `provider_config.py:126`.

**Step 4: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/provider_config.py commander/tests/test_provider_config.py commander/tests/test_fast_model.py
```

Expected: no output.

---

## Task 4: Family-word Codex models in the Commander (R2b) (depends on 3)

**Files:**
- Modify `commander/src/ironclaude/config.py`, lines 91-100: the DEFAULTS codex models.
- Modify `commander/src/ironclaude/provider_config.py`:
  - the imports;
  - lines 21-71, from `EXPECTED_MODELS` through `_codex_models_valid`;
  - the error text at lines 173-177;
  - `model_for` and `advisor_model_for`.
- Modify `commander/src/ironclaude/provider_router.py`: the imports and `resolve`.
- Modify `commander/src/ironclaude/codex_brain_client.py`:
  - the imports at lines 39-48;
  - the default `model` at line 175;
  - `_resolve_model` (lines 263-273);
  - the tier lookup in `_app_server_argv` (lines 281-289);
  - the comment at lines 297-299.
- Modify `commander/src/ironclaude/fast_model.py`: the docstring at lines 11-14, the import at line 29 and `fast_model_clients` at lines 96-99.
- Modify `commander/src/ironclaude/orchestrator_mcp.py`:
  - the import at line 54;
  - `_resolve_remote_worker_client` (lines 644-646), plus a new `_read_remote_codex_cache` method after it;
  - the `_call_grader` docstring (lines 1269-1270);
  - the `resume_session` codex branch (lines 6851-6855).
- Modify `CODEX_SETUP.md`, lines 41-55 and 134-139.
- Modify `config/ironclaude.json.example`, lines 48-51: the codex `models` values.
- Modify tests:
  - `commander/tests/conftest.py`: `_redirect_home`, lines 256-277;
  - `commander/tests/test_provider_config.py`;
  - `commander/tests/test_provider_router.py`;
  - `commander/tests/test_codex_brain_client.py`: `TestModelWiring`, `TestCodexEffortByTier`, and the two R14 trim tests at lines 1507-1528 and 1531-1550;
  - `commander/tests/test_fast_model.py`;
  - `commander/tests/test_orchestrator_mcp.py`.

**Step 1: Tests (RED)**

In `commander/tests/conftest.py` `_redirect_home`:
- In the docstring, replace `CODEX_HOME points at an empty per-test directory.` with `CODEX_HOME holds only the fixture models_cache.json.`
- Replace lines 271-277, from `    # R14: CodexBrainClient reads $CODEX_HOME/config.toml` through `    monkeypatch.setenv("CODEX_HOME", str(codex_home))`, with:

```python
    # R14: CodexBrainClient reads $CODEX_HOME/config.toml (else ~/.codex); a developer's
    # CODEX_HOME must not leak their real Codex config or installed models. It points at a
    # per-test directory whose only file is a fixture models_cache.json, so the Codex family-word
    # defaults (design 2026-10-17 v120-completeness R2) resolve deterministically.
    import json as _json
    codex_home = tmp_path / "ic-codex-home"
    codex_home.mkdir(exist_ok=True)
    (codex_home / "models_cache.json").write_text(_json.dumps(
        {"models": [{"slug": slug} for slug in FIXTURE_CODEX_MODELS]}))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
```

Directly above `@pytest.fixture(autouse=True)` for `_redirect_home`, add:

```python
# The fixture Codex models every test sees in $CODEX_HOME/models_cache.json.
FIXTURE_CODEX_MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")


```

In `commander/tests/test_provider_config.py`:
- Add these parametrize rows to `test_codex_rejects_wrong_tier_word_or_shape` after `("fable", None),`: `("sonnet", "sol"),`, `("opus", "Sol"),`, `("opus", "sol-mini"),` and `("haiku", "gpt-luna"),`.
- Replace everything from line 307 (`# ── Newest installed Codex model (design 2026-10-17 tool-audit-fixes R7) ──`) to the end of the file with the block below. That removes `test_codex_model_for_uses_newest_installed_by_numeric_version`, `test_newest_installed_codex_model_falls_back_without_a_usable_cache`, `test_newest_installed_codex_model_defaults_to_home_dot_codex` and `test_codex_home_is_an_empty_dir_for_every_test`, and keeps `test_model_for_requires_host_keyword` from Task 3.

```python
# ── Codex family words, no pinned models (design 2026-10-17 v120-completeness R2) ──

_WORDS = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}


def _codex_cache(directory, slugs):
    import json
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "models_cache.json").write_text(json.dumps(
        {"models": [{"slug": slug} for slug in slugs] + [{"slug": None}, {}]}))
    return directory


def test_codex_defaults_are_family_words():
    from ironclaude import provider_config
    assert DEFAULTS["providers"]["clients"]["codex"]["models"] == _WORDS
    assert "codex" not in provider_config.EXPECTED_MODELS


def test_codex_accepts_family_words_for_their_tier(base_config):
    raw = base_config()
    raw["clients"]["codex"]["models"] = dict(_WORDS)
    parse_provider_config(raw)
    raw["clients"]["codex"]["models"]["opus"] = "gpt-7.2-sol"  # a word and a slug may mix
    parse_provider_config(raw)


def test_codex_family_word_resolves_to_newest_installed_and_slug_wins(base_config, tmp_path, monkeypatch):
    home = _codex_cache(tmp_path / "codexhome", [
        "gpt-5.9-luna", "gpt-5.10-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-reserve", "codex-auto-review"])
    monkeypatch.setenv("CODEX_HOME", str(home))
    raw = base_config()
    raw["clients"]["codex"]["models"] = {**_WORDS, "fable": "gpt-6-astra"}
    cfg = parse_provider_config(raw)
    assert cfg.model_for("codex", "haiku", host="local") == "gpt-5.10-luna"  # 5.10 > 5.9 numerically
    assert cfg.model_for("codex", "opus", host="local") == "gpt-6.1-sol"
    assert cfg.advisor_model_for("codex", "claude-opus", "opus", host="local") == "gpt-6.1-sol"
    assert cfg.model_for("codex", "fable", host="local") == "gpt-6-astra"  # explicit slug, not installed: as-is
    assert cfg.model_for("claude", "haiku", host="local") == "haiku"  # Claude is untouched
    # An explicit slug wins over a newer installed model, locally and remotely.
    slugs = parse_provider_config(base_config())
    assert slugs.model_for("codex", "opus", host="local") == "gpt-5.6-sol"
    assert slugs.model_for("codex", "opus", host="box-1") == "gpt-5.6-sol"


def test_codex_family_with_no_installed_model_is_unavailable(base_config, tmp_path, monkeypatch):
    from ironclaude.provider_config import CodexModelUnavailable, resolve_codex_model
    home = _codex_cache(tmp_path / "codexhome", ["gpt-6-sol"])
    monkeypatch.setenv("CODEX_HOME", str(home))
    raw = base_config()
    raw["clients"]["codex"]["models"] = dict(_WORDS)
    cfg = parse_provider_config(raw)
    with pytest.raises(CodexModelUnavailable) as exc:
        cfg.model_for("codex", "sonnet", host="local")
    assert exc.value.word == "terra"
    assert str(home / "models_cache.json") in str(exc.value)
    for text in (None, "{not json", "[1, 2]", '{"models": "x"}', '{"models": [{"slug": "gpt-6-sol"}]}'):
        with pytest.raises(CodexModelUnavailable):
            resolve_codex_model("luna", text, "somewhere")
    assert resolve_codex_model("gpt-6-luna", None, "somewhere") == "gpt-6-luna"  # a slug needs no cache


def test_remote_family_word_resolves_from_the_remote_cache(base_config, tmp_path, monkeypatch):
    import json
    from ironclaude.provider_config import CodexModelUnavailable
    monkeypatch.setenv("CODEX_HOME", str(_codex_cache(tmp_path / "local", ["gpt-6.2-sol"])))
    raw = base_config()
    raw["clients"]["codex"]["models"] = dict(_WORDS)
    cfg = parse_provider_config(raw)
    seen = []

    def remote(host):
        seen.append(host)
        return json.dumps({"models": [{"slug": "gpt-6-sol"}, {"slug": "gpt-5.6-sol"}]})

    assert cfg.model_for("codex", "opus", host="box-1", codex_cache=remote) == "gpt-6-sol"
    assert seen == ["box-1"]
    with pytest.raises(CodexModelUnavailable) as exc:  # no remote reader: never this Mac's cache
        cfg.model_for("codex", "opus", host="box-1")
    assert "box-1:" in str(exc.value)


def test_local_codex_cache_defaults_to_home_dot_codex(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from ironclaude.provider_config import resolve_local_codex_model
    monkeypatch.delenv("CODEX_HOME", raising=False)
    _codex_cache(Path(os.environ["HOME"]) / ".codex", ["gpt-6-luna", "gpt-5.6-luna"])
    assert resolve_local_codex_model("luna") == "gpt-6-luna"


def test_codex_home_holds_only_the_fixture_cache():
    """conftest _redirect_home gives every test a CODEX_HOME holding only the fixture cache, so a
    developer's installed Codex models never change what the suite resolves."""
    import json
    import os
    home = os.environ["CODEX_HOME"]
    assert os.listdir(home) == ["models_cache.json"]
    with open(os.path.join(home, "models_cache.json")) as handle:
        assert [m["slug"] for m in json.load(handle)["models"]] == [
            "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"]
    cfg = provider_config_from_commander(copy.deepcopy(DEFAULTS))
    assert cfg.model_for("codex", "haiku", host="local") == "gpt-5.6-luna"


def test_model_for_requires_host_keyword(base_config):
    """Design 2026-10-17 v120-completeness R2: a caller that omits host fails instead of silently
    resolving on this Mac."""
    cfg = parse_provider_config(base_config())
    with pytest.raises(TypeError):
        cfg.model_for("codex", "opus")
    with pytest.raises(TypeError):
        cfg.advisor_model_for("codex", "claude-opus", "opus")
    assert cfg.model_for("codex", "opus", host="local") == "gpt-5.6-sol"  # positive control
```

In `commander/tests/test_provider_router.py`, replace everything from line 421 (`# ── Newest installed Codex model, local hosts only ...`) to the end of the file with:

```python
# ── Codex family words resolve where Codex runs (design 2026-10-17 v120-completeness R2) ──

def test_codex_family_words_resolve_where_codex_runs_and_unavailable_falls_through(
    tmp_path, base_config, monkeypatch, caplog,
):
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "models_cache.json").write_text('{"models": [{"slug": "gpt-6.1-sol"}, {"slug": "gpt-6-sol"}]}')
    monkeypatch.setenv("CODEX_HOME", str(home))
    raw = base_config()
    raw["clients"]["codex"]["enabled"] = True
    raw["clients"]["codex"]["models"] = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}
    for role in raw["roles"].values():
        role["clients"] = ["claude", "codex"]
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    router = ProviderRouter(parse_provider_config(raw), state, registry)
    state.set_current_client("worker", "codex")
    state.set_current_client("advisor", "codex")
    record(
        registry,
        cap("codex", "worker", "opus"),
        cap("codex", "worker", "opus", host="box-1"),
        cap("codex", "advisor", "opus"),
        cap("codex", "worker", "sonnet"),
        cap("claude", "worker", "sonnet"),
    )
    local = router.resolve("worker", "opus", ["local"])
    remote = router.resolve("worker", "opus", ["box-1"],
                            codex_cache=lambda host: '{"models": [{"slug": "gpt-6-sol"}]}')
    assert (local.client, local.host, local.model) == ("codex", "local", "gpt-6.1-sol")
    assert (remote.client, remote.host, remote.model) == ("codex", "box-1", "gpt-6-sol")
    assert router.resolve("advisor", "opus", ["local"], advisor_worker_type="claude-opus").model == "gpt-6.1-sol"
    # No Terra installed: Codex is unavailable for the call and routing falls to Claude.
    fallback = router.resolve("worker", "sonnet", ["local"])
    assert (fallback.client, fallback.model) == ("claude", "sonnet")
    assert "'terra'" in caplog.text and str(home / "models_cache.json") in caplog.text
```

In `commander/tests/test_codex_brain_client.py`, replace `test_brain_model_is_newest_installed_with_table_fallback` (lines 494-506, inside `class TestModelWiring`) with:

```python
    def test_brain_family_resolves_to_newest_installed_and_slug_wins(self, tmp_path, monkeypatch):
        """Design 2026-10-17 v120-completeness R2: the Brain runs on this Mac, so a tier or family
        word resolves to the newest installed model of the family; an explicit slug is used as-is."""
        home = tmp_path / "codexhome"
        home.mkdir()
        (home / "models_cache.json").write_text(json.dumps(
            {"models": [{"slug": "gpt-6.1-sol"}, {"slug": "gpt-6-sol"}, {"slug": "gpt-6-luna"}]}))
        monkeypatch.setenv("CODEX_HOME", str(home))
        assert CodexBrainClient(model="opus")._resolve_model() == "gpt-6.1-sol"
        assert CodexBrainClient(model="sol")._resolve_model() == "gpt-6.1-sol"
        assert CodexBrainClient(model="gpt-5.6-sol")._resolve_model() == "gpt-5.6-sol"
        assert CodexBrainClient(model="haiku")._resolve_model() == "gpt-6-luna"
        assert 'model="gpt-6.1-sol"' in CodexBrainClient(model="opus")._app_server_argv()

    def test_brain_family_with_no_installed_model_fails_start_visibly(self, tmp_path, monkeypatch):
        import ironclaude.codex_brain_client as module
        from ironclaude.provider_config import CodexModelUnavailable
        home = tmp_path / "codexhome"
        home.mkdir()
        (home / "models_cache.json").write_text(json.dumps({"models": [{"slug": "gpt-6-sol"}]}))
        monkeypatch.setenv("CODEX_HOME", str(home))
        client = CodexBrainClient(model="sonnet")
        with pytest.raises(CodexModelUnavailable):
            client._resolve_model()
        monkeypatch.setattr(client, "_runtime_preflight", lambda: {
            "schema_version": 1, "status": "healthy", "reason": "destination-equivalent",
            "source_companion": "/s", "destination_companion": "/d",
        })
        monkeypatch.setattr(client, "_preflight_orchestrator", lambda: None)
        popen = MagicMock()
        monkeypatch.setattr(module.subprocess, "Popen", popen)
        client.start("system prompt", cwd="/tmp")
        popen.assert_not_called()
        assert client._running is False
        assert "Codex model family 'terra' has no installed model in" in client.restart_reason

    def test_default_model_is_the_sonnet_family(self):
        import inspect
        assert inspect.signature(CodexBrainClient).parameters["model"].default == "sonnet"
```

Append inside `class TestCodexEffortByTier`, after `test_sol_codex_model_falls_back_to_global_effort`:

```python

    def test_any_astra_slug_resolves_per_tier_effort(self):
        c = CodexBrainClient(
            model="gpt-7-astra", effort_level="high", effort_levels={"fable": "medium"}
        )
        assert 'model_reasoning_effort="medium"' in c._app_server_argv()
        assert 'model="gpt-7-astra"' in c._app_server_argv()
```

The two R14 trim tests point `CODEX_HOME` at a directory holding only a `config.toml`. `_app_server_argv` evaluates `model="{self._resolve_model()}"` before `*self._tool_trim_overrides()`, so with the new `"sonnet"` default the `terra` family would be unavailable there and raise before the trim is derived. Pin an explicit slug in both, so they keep testing only the trim:
- In `test_app_server_argv_trims_inherited_tools`, replace lines 1516-1517 (the two-line anchor is unique in the file; the single `CodexBrainClient()` line is not):

```python
    monkeypatch.setenv("CODEX_HOME", str(home))
    argv = CodexBrainClient()._app_server_argv()
```

  with:

```python
    monkeypatch.setenv("CODEX_HOME", str(home))
    argv = CodexBrainClient(model="gpt-5.6-terra")._app_server_argv()  # explicit slug: no models cache needed
```

- In `test_start_fails_closed_when_trim_cannot_be_derived`, replace lines 1538-1539:

```python
    monkeypatch.setenv("CODEX_HOME", str(home))
    client = CodexBrainClient()
```

  with:

```python
    monkeypatch.setenv("CODEX_HOME", str(home))
    client = CodexBrainClient(model="gpt-5.6-terra")  # explicit slug: only the trim can fail
```

Both pass before and after the change: today an explicit slug with no cache falls back to itself, and after the change an explicit slug is used as-is.

In `commander/tests/test_fast_model.py`, replace `test_codex_fast_clients_use_newest_installed_model` (lines 424-434) with:

```python
def test_codex_fast_clients_resolve_family_words_and_skip_an_unavailable_family(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(
        {"models": [{"slug": "gpt-6-luna"}, {"slug": "gpt-5.10-terra"}, {"slug": "gpt-5.9-terra"}]}))
    monkeypatch.setenv("CODEX_HOME", str(home))
    cfg = _cfg_classes(preferred="codex", clients=("codex", "claude"), codex_enabled=True)
    cfg["providers"]["clients"]["codex"]["models"] = {"haiku": "luna", "sonnet": "terra", "opus": "sol"}
    assert fast_model.fast_model_clients(cfg) == [
        ("codex", "/bin/codex-x", "gpt-6-luna"), ("claude", "/bin/claude-x", "haiku")]
    assert fast_model.fast_model_clients(cfg, "sonnet") == [
        ("codex", "/bin/codex-x", "gpt-5.10-terra"), ("claude", "/bin/claude-x", "sonnet")]
    # No Sol installed: Codex is unavailable for the call and Claude answers.
    assert fast_model.fast_model_clients(cfg, "opus") == [("claude", "/bin/claude-x", "opus")]
    # An explicit slug wins over a newer installed model.
    slugs = _cfg_classes(preferred="codex", clients=("codex", "claude"), codex_enabled=True)
    assert fast_model.fast_model_clients(slugs)[0] == ("codex", "/bin/codex-x", "gpt-5.6-luna")
```

In `commander/tests/test_orchestrator_mcp.py`:
- After `_VALID_NATIVE_UUID = ...` (line 41), add:

```python
_REMOTE_CODEX_CACHE_ARGV = ("sh", "-c", 'cat "${CODEX_HOME:-$HOME/.codex}/models_cache.json"')


def _cache(*slugs):
    return json.dumps({"models": [{"slug": slug} for slug in slugs]})
```

- In `TestRemoteProviderLaunching.test_remote_provider_resolution_is_host_scoped`:
  - In `run_argv`, insert `            if tuple(argv) == _REMOTE_CODEX_CACHE_ARGV:` and `                return ProbeResult(0, _cache("gpt-5.7-terra", "gpt-5.6-terra"), "")` before `            return ProbeResult(0, "version\n", "")`.
  - Change `assert handle.model == "gpt-5.6-terra"` to `assert handle.model == "gpt-5.7-terra"  # the remote box's own newest Terra`.
  - After `assert handle.host == "ssh-remote"`, add `        assert _REMOTE_CODEX_CACHE_ARGV in [tuple(call.args[1]) for call in ssh.run_argv.call_args_list]`.
- In `test_remote_codex_fable_resolution_survives_claude_fable_quarantine`:
  - In `run_argv`, insert `            if tuple(argv) == _REMOTE_CODEX_CACHE_ARGV:` and `                return ProbeResult(0, _cache("gpt-6.5-astra", "gpt-6-sol"), "")` before its final `return`.
  - Change the expected tuple's model `"gpt-6-astra"` to `"gpt-6.5-astra"`.
- In `test_remote_worker_capability_recovery_probes_after_explicit_cutover`, in `run_argv`, insert `            if tuple(argv) == _REMOTE_CODEX_CACHE_ARGV:` and `                return ProbeResult(0, _cache("gpt-5.6-terra"), "")` before its final `return`.
- Append these two methods to `class TestRemoteProviderLaunching`, directly after `test_remote_worker_capability_recovery_probes_after_explicit_cutover`:

```python

    def test_remote_codex_family_missing_falls_back_to_claude(
        self, registry, mock_tmux, tmp_path, db_conn, caplog,
    ):
        """Design 2026-10-17 v120-completeness R2: no Terra on the remote box -> Codex is
        unavailable for the call and routing falls to Claude, with a log naming family and cache."""
        config = copy.deepcopy(DEFAULTS)
        config["providers"]["clients"]["codex"]["enabled"] = True
        config["providers"]["roles"]["worker"] = {"preferred": "codex", "clients": ["claude", "codex"]}
        tools = OrchestratorTools(registry, mock_tmux, str(tmp_path / "ledger.json"),
                                  db_conn=db_conn, config=config)
        machine = self._machine({
            "claude": {"enabled": True, "path": "/opt/claude"},
            "codex": {"enabled": True, "path": "/opt/codex"},
        })
        ssh = self._attach_remote(tools, machine)

        def run_argv(_host, argv):
            if tuple(argv) == ("/opt/codex", "login", "status"):
                return ProbeResult(0, "Logged in using ChatGPT\n", "")
            if tuple(argv) == _REMOTE_CODEX_CACHE_ARGV:
                return ProbeResult(0, _cache("gpt-6-sol"), "")
            return ProbeResult(0, "version\n", "")

        ssh.run_argv.side_effect = run_argv
        ProviderState(db_conn).set_current_client("worker", "codex")
        handle = tools._resolve_remote_worker_client("claude-sonnet", machine)
        assert (handle.client, handle.model, handle.host) == ("claude", "sonnet", "ssh-remote")
        assert "'terra'" in caplog.text and "ssh-remote:" in caplog.text

    def test_remote_codex_cache_read_honours_machine_codex_home(
        self, registry, mock_tmux, tmp_path, db_conn,
    ):
        config = copy.deepcopy(DEFAULTS)
        config["providers"]["clients"]["codex"]["enabled"] = True
        config["providers"]["roles"]["worker"] = {"preferred": "codex", "clients": ["codex"]}
        tools = OrchestratorTools(registry, mock_tmux, str(tmp_path / "ledger.json"),
                                  db_conn=db_conn, config=config)
        machine = self._machine({"codex": {"enabled": True, "path": "/opt/codex"}},
                                env={"CODEX_HOME": "/srv/codex"})
        ssh = self._attach_remote(tools, machine)
        expected = ("env", "CODEX_HOME=/srv/codex", *_REMOTE_CODEX_CACHE_ARGV)

        def run_argv(_host, argv):
            if tuple(argv) == ("/opt/codex", "login", "status"):
                return ProbeResult(0, "Logged in using ChatGPT\n", "")
            if tuple(argv) == expected:
                return ProbeResult(0, _cache("gpt-5.8-terra"), "")
            return ProbeResult(0, "version\n", "")

        ssh.run_argv.side_effect = run_argv
        handle = tools._resolve_remote_worker_client("claude-sonnet", machine)
        assert (handle.client, handle.model) == ("codex", "gpt-5.8-terra")
```

- Append this method to `class TestResumeSession`, directly after `test_success_uses_exact_client_and_persists_native_identity`:

```python

    def test_codex_resume_without_an_installed_family_model_fails_before_spawn(
        self, tools, mock_tmux, tmp_path, monkeypatch,
    ):
        self._prepare_success(tools, mock_tmux, "codex")
        empty = tmp_path / "empty-codex-home"
        empty.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(empty))
        out = tools.resume_session(self.UUID, "codex", "d6", repo="/r")
        assert "Codex model family 'sol' has no installed model in" in out["error"]
        mock_tmux.spawn_session.assert_not_called()
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_provider_config.py tests/test_provider_router.py tests/test_codex_brain_client.py tests/test_fast_model.py tests/test_orchestrator_mcp.py -q > /private/tmp/claude-502/taf2/t4-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t4-red.log; tail -n 1 /private/tmp/claude-502/taf2/t4-red.log
```

Expected: `red=1`. FAILED is exactly these 19. Traced against the current source with the fixture cache, which equals today's configured slugs. The current local resolver replaces any configured slug with the newest installed model of its word, so an explicit slug that is not in the fixture cache fails today:

`test_provider_config.py`:
- `test_codex_accepts_gpt6_models` (`gpt-6-sol` is replaced by the fixture's `gpt-5.6-sol`)
- `test_codex_accepts_future_versions` (`gpt-7.1-sol` is replaced by `gpt-5.6-sol`)
- `test_codex_defaults_are_family_words`
- `test_codex_accepts_family_words_for_their_tier`
- `test_codex_family_word_resolves_to_newest_installed_and_slug_wins`
- `test_codex_family_with_no_installed_model_is_unavailable` (ImportError)
- `test_remote_family_word_resolves_from_the_remote_cache` (unknown `codex_cache` keyword)
- `test_local_codex_cache_defaults_to_home_dot_codex` (ImportError)

`test_provider_router.py`:
- `test_codex_family_words_resolve_where_codex_runs_and_unavailable_falls_through`

`test_codex_brain_client.py`:
- `TestModelWiring::test_brain_family_resolves_to_newest_installed_and_slug_wins`
- `TestModelWiring::test_brain_family_with_no_installed_model_fails_start_visibly`
- `TestModelWiring::test_default_model_is_the_sonnet_family`
- `TestCodexEffortByTier::test_any_astra_slug_resolves_per_tier_effort` (effort `high`, model replaced by `gpt-6-astra`)

`test_fast_model.py`:
- `test_codex_fast_clients_resolve_family_words_and_skip_an_unavailable_family`

`test_orchestrator_mcp.py`:
- `TestRemoteProviderLaunching::test_remote_provider_resolution_is_host_scoped`
- `TestRemoteProviderLaunching::test_remote_codex_fable_resolution_survives_claude_fable_quarantine`
- `TestRemoteProviderLaunching::test_remote_codex_family_missing_falls_back_to_claude`
- `TestRemoteProviderLaunching::test_remote_codex_cache_read_honours_machine_codex_home`
- `TestResumeSession::test_codex_resume_without_an_installed_family_model_fails_before_spawn`

`test_codex_home_holds_only_the_fixture_cache`, the new negative parametrize rows and the two R14 trim tests pass both before and after the change.

**Step 2: Implement (GREEN)**

Make every edit below, then run the one verification at the end of this step. The edits are not separately runnable: the conftest autouse fixture imports `fast_model`, which imports from `provider_config`.

*provider_config and config (family words, resolution, required host).* In `commander/src/ironclaude/config.py`, the codex `models` block (lines 94-99) becomes:

```python
                "models": {
                    "haiku": "luna",
                    "sonnet": "terra",
                    "opus": "sol",
                    "fable": "astra",
                },
```

In `commander/src/ironclaude/provider_config.py`:
- Change `from typing import Mapping` to `from typing import Callable, Mapping`.
- Replace lines 21-71, from `EXPECTED_MODELS = {` through the end of `_codex_models_valid`, with:

```python
EXPECTED_MODELS = {
    "claude": {
        "haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"
    },
}
# Codex models are family words (design 2026-10-17 v120-completeness R2; operator: "we shouldn't be
# doing any pinned defaults in ironclaude anymore"). Like Claude's aliases, a word means the newest
# model of that family where Codex runs; an explicit gpt-<version>-<word> slug is used as-is.
CODEX_TIER_WORDS = {"haiku": "luna", "sonnet": "terra", "opus": "sol", "fable": "astra"}
CODEX_MODEL_RE = re.compile(r"^gpt-(\d+(?:\.\d+)*)-(luna|terra|sol|astra)$")
# Read on a remote worker host through SSHConnectionManager.run_argv, which shell-quotes every
# argument, so `sh -c` expands the cache path on that host.
REMOTE_CODEX_CACHE_ARGV = ("sh", "-c", 'cat "${CODEX_HOME:-$HOME/.codex}/models_cache.json"')


class CodexModelUnavailable(ValueError):
    """No installed model of a Codex family word: Codex is unavailable for this call."""

    def __init__(self, word: str, cache_location: str):
        self.word = word
        self.cache_location = cache_location
        super().__init__(
            f"Codex model family {word!r} has no installed model in {cache_location}; "
            "Codex is unavailable for this call"
        )


def local_codex_cache_path() -> str:
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(home, "models_cache.json")


def remote_codex_cache_location(host: str) -> str:
    return f"{host}:${{CODEX_HOME:-$HOME/.codex}}/models_cache.json"


def read_local_codex_cache() -> str | None:
    try:
        with open(local_codex_cache_path(), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def newest_codex_slug(word: str, cache_text: str | None) -> str | None:
    """Newest ``gpt-<version>-<word>`` listed in a Codex models_cache.json text, ordered by numeric
    version like plan-validator.sh ``_newest_codex_model`` and advisor-review.ts ``newestAtTier``.
    None when the text is missing or malformed or lists no model of the family."""
    if cache_text is None:
        return None
    try:
        models = json.loads(cache_text).get("models")
    except (ValueError, AttributeError):
        return None
    best = None
    for entry in models if isinstance(models, list) else ():
        slug = entry.get("slug") if isinstance(entry, dict) else None
        match = CODEX_MODEL_RE.fullmatch(slug) if isinstance(slug, str) else None
        if match is None or match.group(2) != word:
            continue
        version = tuple(int(part) for part in match.group(1).split("."))
        if best is None or version > best[0]:
            best = (version, slug)
    return best[1] if best else None


def resolve_codex_model(value: str, cache_text: str | None, cache_location: str) -> str:
    """An explicit ``gpt-<version>-<word>`` slug is used as-is. A family word resolves to the newest
    installed model of that family in ``cache_text``, else CodexModelUnavailable."""
    if CODEX_MODEL_RE.fullmatch(value):
        return value
    newest = newest_codex_slug(value, cache_text)
    if newest is None:
        raise CodexModelUnavailable(value, cache_location)
    return newest


def resolve_local_codex_model(value: str) -> str:
    """resolve_codex_model against this Mac's ``$CODEX_HOME/models_cache.json`` (default ~/.codex)."""
    return resolve_codex_model(value, read_local_codex_cache(), local_codex_cache_path())


def _codex_models_valid(models: Mapping[str, object]) -> bool:
    if set(models) != set(TIER_NAMES):
        return False
    for tier, value in models.items():
        word = CODEX_TIER_WORDS[tier]
        if value == word:
            continue
        match = CODEX_MODEL_RE.fullmatch(value) if isinstance(value, str) else None
        if match is None or match.group(2) != word:
            return False
    return True
```

- Replace `ProviderConfig.model_for` and `advisor_model_for` with:

```python
    def model_for(
        self, client: str, tier: str, role: str | None = None, *, host: str,
        codex_cache: Callable[[str], str | None] | None = None,
    ) -> str:
        if client == "claude" and role is not None:
            override = self.claude_role_models.get((role, tier))
            if override is not None:
                return override
        try:
            model = self.clients[client].models[tier]
        except KeyError as exc:
            raise ProviderConfigError(
                f"client {client!r} has no model mapping for tier {tier!r}"
            ) from exc
        # Design 2026-10-17 v120-completeness R2: Claude aliases and explicit Codex slugs are used
        # as-is, locally and remotely. A Codex family word resolves where Codex runs: this Mac's
        # cache on host "local", the remote box's own cache through codex_cache(host) otherwise.
        # No model of the family -> CodexModelUnavailable (the router tries the next client).
        if client != "codex" or CODEX_MODEL_RE.fullmatch(model):
            return model
        if host == "local":
            return resolve_local_codex_model(model)
        return resolve_codex_model(
            model, codex_cache(host) if codex_cache is not None else None,
            remote_codex_cache_location(host),
        )

    def advisor_model_for(
        self, client: str, worker_type: str, tier: str, *, host: str,
        codex_cache: Callable[[str], str | None] | None = None,
    ) -> str:
        if client == "claude":
            override = self.advisor_worker_models.get(worker_type)
            if override is not None:
                return override
        return self.model_for(client, tier, "advisor", host=host, codex_cache=codex_cache)
```

- Replace the error text at lines 173-177 with:

```python
                raise ProviderConfigError(
                    "providers.clients.codex.models must map each tier to its Codex family word "
                    "or a gpt-<version>-<tier-word> model (haiku→luna, sonnet→terra, "
                    "opus→sol, fable→astra)"
                )
```

*Router, Brain, fast children and orchestrator.* In `commander/src/ironclaude/provider_router.py`:
- Add `import logging` after `from dataclasses import dataclass`.
- Change `from typing import Sequence` to `from typing import Callable, Sequence`.
- Change `from ironclaude.provider_config import ProviderConfig` to `from ironclaude.provider_config import CodexModelUnavailable, ProviderConfig`.
- After the imports, add `logger = logging.getLogger(__name__)`.
- In `resolve`, add the keyword `codex_cache: Callable[[str], str | None] | None = None,` after `excluded_clients: Sequence[str] = (),`.
- Replace the model lookup (lines 81-87) with:

```python
                try:
                    model = (
                        self._config.advisor_model_for(
                            client, advisor_worker_type, effective_tier,
                            host=host, codex_cache=codex_cache,
                        )
                        if role == "advisor" and advisor_worker_type is not None
                        else self._config.model_for(
                            client, effective_tier, role, host=host, codex_cache=codex_cache,
                        )
                    )
                except CodexModelUnavailable as exc:
                    # Design 2026-10-17 v120-completeness R2: try the next client/tier.
                    logger.warning("Provider route skipped: %s", exc)
                    continue
```

In `commander/src/ironclaude/codex_brain_client.py`:
- Line 39 becomes `from ironclaude.config import effort_for_tier`.
- Line 41 becomes `from ironclaude.provider_config import CODEX_MODEL_RE, CODEX_TIER_WORDS, resolve_local_codex_model`.
- Delete lines 45-48: the `# Codex tier->model map` comment and `_CODEX_TIER_MODELS = ...`.
- The default at line 175 becomes `model: str = "sonnet",`.
- Replace `_resolve_model` with:

```python
    def _resolve_model(self) -> str:
        """Tier name (haiku/sonnet/opus/fable) -> its Codex family word. A family word resolves to
        the newest installed model of that family on this Mac; an explicit gpt-<version>-<word>
        slug is used as-is (design 2026-10-17 v120-completeness R2). Raises CodexModelUnavailable
        (a ValueError, so start() fails visibly) when no model of the family is installed. Any
        other value passes through unchanged."""
        value = CODEX_TIER_WORDS.get(self._model, self._model)
        if value in CODEX_TIER_WORDS.values():
            return resolve_local_codex_model(value)
        return value
```

- In `_app_server_argv`, replace the comment and tier lookup (lines 281-289) with:

```python
        # TIER-FIRST reasoning-effort: self._model may be a tier name, a family word or a
        # gpt-<version>-<word> slug. Map it to its family word, then the word to its tier, and let
        # effort_levels override the global effort_level per tier.
        word = CODEX_TIER_WORDS.get(self._model, self._model)
        match = CODEX_MODEL_RE.fullmatch(word) if isinstance(word, str) else None
        if match is not None:
            word = match.group(2)
        tier = {family: name for name, family in CODEX_TIER_WORDS.items()}.get(word, "")
```

- Replace the comment at lines 297-299 with:

```python
            # R12: no subagents for the Brain. agents.enabled=false makes Codex's
            # multi-agent version Disabled ahead of the model catalog (which gives
            # Terra models the v2 "collaboration" tools), so spawn_agent is never offered.
```

In `commander/src/ironclaude/fast_model.py`:
- Replace docstring lines 11-14, from `The model class is per call (design T1)` through `box; LocalGrader routes them here directly.`, with:

```text
The model class is per call (design T1): `tier="haiku"` (default) or `tier="sonnet"`.
Each client's `models.<tier>` names the model. On Codex that is a family word (`luna`, `terra`)
resolved to the newest installed model of the family, or an explicit slug used as-is; with no
model of the family installed, Codex is skipped for the call. Sonnet-class calls never use the
local box; LocalGrader routes them here directly.
```

- Line 29 becomes `from ironclaude.provider_config import CodexModelUnavailable, resolve_local_codex_model`.
- In `fast_model_clients`, replace:

```python
            model = client["models"][tier]
            if name == "codex":
                model = newest_installed_codex_model(tier, model)
            out.append((name, client.get("path") or name, model))
```

with:

```python
            model = client["models"][tier]
            if name == "codex":
                try:
                    model = resolve_local_codex_model(model)
                except CodexModelUnavailable as exc:
                    logger.warning("fast model skips codex: %s", exc)
                    continue
            out.append((name, client.get("path") or name, model))
```

In `commander/src/ironclaude/orchestrator_mcp.py`:
- Line 54 becomes `from ironclaude.provider_config import provider_config_from_commander, _semantic_tier, ProviderConfigError, CODEX_TIER_WORDS, REMOTE_CODEX_CACHE_ARGV, CodexModelUnavailable, resolve_local_codex_model`.
- In `_resolve_remote_worker_client`, replace:

```python
        return router.resolve(
            "worker", tier, [machine_cfg.host],
        )
```

with:

```python
        return router.resolve(
            "worker", tier, [machine_cfg.host],
            codex_cache=lambda _host: self._read_remote_codex_cache(machine_cfg),
        )

    def _read_remote_codex_cache(self, machine_cfg) -> str | None:
        """The remote box's own Codex models_cache.json (design 2026-10-17 v120-completeness R2:
        a family word resolves where Codex runs). None when the read fails, so the router treats
        Codex as unavailable for the call."""
        argv = list(REMOTE_CODEX_CACHE_ARGV)
        codex_home = (machine_cfg.env or {}).get("CODEX_HOME")
        if codex_home:
            argv = ["env", f"CODEX_HOME={codex_home}", *argv]
        try:
            result = self._ssh_manager.run_argv(machine_cfg.host, argv)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("Remote Codex models cache read failed on %s: %s", machine_cfg.host, exc)
            return None
        return result.stdout if result.returncode == 0 else None
```

- In the `_call_grader` docstring, replace lines 1269-1270:

```text
        through the same router: each client's ``models.<tier>`` (Claude ``sonnet``;
        Codex ``gpt-5.6-terra`` by default). ``None`` keeps the configured grader class
```

with:

```text
        through the same router: each client's ``models.<tier>`` (Claude ``sonnet``; Codex
        the ``terra`` family word, resolved to the newest installed Terra). ``None`` keeps the configured grader class
```
- In `resume_session`, replace:

```python
            # Same flags as the fresh spawn (_codex_worker_command) and the handoff resume:
            # an explicit model (newest installed, else configured) and --no-daemon.
            tier = _WORKER_TYPE_TIER.get(worker_type, "opus")
            model = newest_installed_codex_model(
                tier, codex_cfg.get("models", {}).get(tier) or EXPECTED_MODELS["codex"][tier])
```

with:

```python
            # Same flags as the fresh spawn (_codex_worker_command) and the handoff resume:
            # an explicit model and --no-daemon. The configured value (family word by default)
            # resolves on this Mac; no model of the family -> refuse before anything spawns.
            tier = _WORKER_TYPE_TIER.get(worker_type, "opus")
            try:
                model = resolve_local_codex_model(
                    codex_cfg.get("models", {}).get(tier) or CODEX_TIER_WORDS[tier])
            except CodexModelUnavailable as exc:
                return {"error": str(exc)}
```

In `CODEX_SETUP.md`:
- Replace lines 41-55, from `IronClaude maps its tier names to codex models` through `when lower tiers cannot reliably handle the assigned work.`, with:

```markdown
IronClaude maps its tier names to Codex model families
(`config/ironclaude.json` → `providers.clients.codex.models`):

| Tier   | Codex default |
|--------|---------------|
| haiku  | `luna`        |
| sonnet | `terra`       |
| opus   | `sol`         |
| fable  | `astra`       |

IronClaude pins no Codex model. Like Claude's `haiku`/`sonnet`/`opus`/`fable` aliases, a family word
means the newest `gpt-<version>-<word>` model that Codex lists in `$CODEX_HOME/models_cache.json`
(default `~/.codex`) on the machine where Codex runs. A remote worker resolves from its own box's
cache. An explicit `gpt-<version>-<word>` slug whose word matches the tier (for example `gpt-6-sol`
for opus) is used as-is. If no model of the family is installed, Codex is unavailable for that call:
routing falls to the next client, and hooks and graders fail open.

`fable` / `astra` is the hardest Codex tier, not the default. Select it only
when lower tiers cannot reliably handle the assigned work.
```

- Replace lines 134-139, from `**Model:** the codex Brain runs` through `(a literal codex model passes through unchanged).`, with:

```markdown
**Model:** the codex Brain runs the newest installed model of the family mapped from your
`brain_model` tier (`sonnet` → Terra, `opus` → Sol, `haiku` → Luna, `fable` → Astra). The Commander
default is `brain_model: "sonnet"`. Override with `BRAIN_MODEL=<gpt-version-word>`; an explicit slug
is used as-is. If no model of the family is installed, the Brain does not start and the daemon log
names the family and the cache path.
```

In `config/ironclaude.json.example`, replace lines 48-51 (the codex `models` values, `"haiku": "gpt-5.6-luna",` through `"fable": "gpt-6-astra"`) with:

```json
          "haiku": "luna",
          "sonnet": "terra",
          "opus": "sol",
          "fable": "astra"
```

`tests/test_config.py::test_shipped_configuration_examples_parse` loads this example through `load_config`, which runs `provider_config_from_commander` and so the new word-or-slug `_codex_models_valid`; each value equals its tier's `CODEX_TIER_WORDS` word, so it passes.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_provider_config.py tests/test_provider_router.py tests/test_codex_brain_client.py tests/test_codex_brain_wiring.py tests/test_codex_brain_hooks_canary.py tests/test_fast_model.py tests/test_fast_model_chain.py tests/test_grader_routing.py tests/test_grader_router.py tests/test_worker_handoff.py tests/test_worker_adapter.py tests/test_config.py tests/test_orchestrator_mcp.py -q > /private/tmp/claude-502/taf2/t4-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t4-green.log; tail -n 1 /private/tmp/claude-502/taf2/t4-green.log; rg -n -e 'gpt-[0-9]' -e 'newest_installed_codex_model' -e '_CODEX_TIER_MODELS' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/config.py /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/provider_config.py /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/codex_brain_client.py /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/fast_model.py /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/provider_router.py /Users/roberthyatt/Code/ironclaude/config/ironclaude.json.example; echo "pinned_rc=$?"
```

Expected:
- `green=0`, 0 failed.
- No rg match lines, and `pinned_rc=1`.

Every other existing test passes unchanged: `base_config` slugs are explicit and used as-is, and the fixture cache resolves the DEFAULTS words to today's slugs. `test_codex_accepts_gpt6_models` and `test_codex_accepts_future_versions` now pass because an explicit slug wins.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/config.py commander/src/ironclaude/provider_config.py commander/src/ironclaude/provider_router.py commander/src/ironclaude/codex_brain_client.py commander/src/ironclaude/fast_model.py commander/src/ironclaude/orchestrator_mcp.py CODEX_SETUP.md config/ironclaude.json.example commander/tests/conftest.py commander/tests/test_provider_config.py commander/tests/test_provider_router.py commander/tests/test_codex_brain_client.py commander/tests/test_fast_model.py commander/tests/test_orchestrator_mcp.py
```

Expected: no output.

---

## Task 5: No pinned Codex model in the hook and the broker (R2c) (depends on 4)

**Files:**
- Modify `worker/hooks/plan-validator.sh`:
  - lines 118-132: `_newest_codex_model`, `_newest_luna` and `_newest_terra`;
  - the two Codex arms of `call_validation_llm`, at lines 251-253 and 379-381.
- Modify `worker/hooks/tests/test-fast-model-fallback.sh`: lines 73-77, 155-157 and 240-241.
- Modify `worker/mcp-servers/state-manager/src/tools/advisor-review.ts`:
  - lines 21-22;
  - lines 227-255, `readInstalledCodexModels` and `resolveCodexReviewer`;
  - lines 617-619, the reviewer model selection.
- Modify `worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts`: lines 684-699 and 713-724.
- Modify `worker/mcp-servers/state-manager/dist/index.js` by rebuilding it.
- Create `commander/tests/test_no_pinned_codex_models.py`.
- Modify `worker/skills/advisor-fallback/SKILL.md`, lines 73-77.
- Modify `worker/config-schema/llm-backend.md`, lines 232 and 249-251.

**Step 1: Tests (RED)**

Create `commander/tests/test_no_pinned_codex_models.py`:

```python
"""Design 2026-10-17 v120-completeness R2: IronClaude pins no Codex model as a default or a
fallback. Family words resolve where Codex runs; tests may still use concrete slugs."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNTIME_SOURCES = (
    "commander/src/ironclaude/config.py",
    "commander/src/ironclaude/provider_config.py",
    "commander/src/ironclaude/provider_router.py",
    "commander/src/ironclaude/codex_brain_client.py",
    "commander/src/ironclaude/fast_model.py",
    "commander/src/ironclaude/orchestrator_mcp.py",
    "worker/hooks/plan-validator.sh",
    "worker/mcp-servers/state-manager/src/tools/advisor-review.ts",
    "worker/mcp-servers/state-manager/dist/index.js",
    "config/ironclaude.json.example",
)
SLUG = re.compile(r"gpt-\d+(?:\.\d+)*-(?:luna|terra|sol|astra)")


def test_no_pinned_codex_model_slugs_in_runtime_sources():
    found = {rel: SLUG.findall((REPO / rel).read_text()) for rel in RUNTIME_SOURCES}
    assert {rel: hits for rel, hits in found.items() if hits} == {}
```

In `worker/hooks/tests/test-fast-model-fallback.sh`:
- After `run_cvl()` (lines 73-77), add:

```bash
# Like run_cvl, with stderr kept in $WORK/err and an optional class ($1, default haiku).
run_cvl_err() {
  reset_logs
  call_validation_llm "grade this" "$SCHEMA" "${1:-haiku}" > "$WORK/out" 2> "$WORK/err"; RC=$?
  BACKEND="$VALIDATION_LLM_BACKEND"
}
```

- Replace lines 155-157:

```bash
echo "=== Codex with no models cache falls back to gpt-6-luna ==="
rm -f "$CODEX_HOME/models_cache.json"; run_cvl
assert_eq "no cache -> luna:gpt-6-luna" "luna:gpt-6-luna" "$BACKEND"
```

with:

```bash
echo "=== Codex with no installed Luna is unavailable for the call (no pinned fallback) ==="
rm -f "$CODEX_HOME/models_cache.json"; run_cvl_err
assert_eq "no cache -> rc 1 (Codex unavailable, fail open)" "1" "$RC"
assert_eq "no cache -> backend label luna:unavailable" "luna:unavailable" "$BACKEND"
assert_eq "no cache -> codex not called" "no" "$(called codex.argv)"
assert_eq "no cache -> stderr names the family and the cache path" "1" \
  "$(grep -cF "[plan-validator] Codex model family luna has no installed model in $CODEX_HOME/models_cache.json; Codex is unavailable for this call" "$WORK/err")"
printf '%s' '{"models":[{"slug":"gpt-6-sol"}]}' > "$CODEX_HOME/models_cache.json"; run_cvl_err
assert_eq "cache without a Luna -> backend label luna:unavailable" "luna:unavailable" "$BACKEND"
printf '%s' '{"models":[{"slug":"gpt-5.9-luna"},{"slug":"gpt-5.10-luna"}]}' > "$CODEX_HOME/models_cache.json"
```

- Replace lines 240-241:

```bash
rm -f "$CODEX_HOME/models_cache.json"; run_cvl_tier sonnet
assert_eq "codex sonnet, no cache -> terra:gpt-5.6-terra" "terra:gpt-5.6-terra" "$BACKEND"
```

with:

```bash
rm -f "$CODEX_HOME/models_cache.json"; run_cvl_err sonnet
assert_eq "codex sonnet, no cache -> rc 1" "1" "$RC"
assert_eq "codex sonnet, no cache -> backend label terra:unavailable" "terra:unavailable" "$BACKEND"
assert_eq "codex sonnet, no cache -> codex not called" "no" "$(called codex.argv)"
assert_eq "codex sonnet, no cache -> stderr names the family and the cache path" "1" \
  "$(grep -cF "[plan-validator] Codex model family terra has no installed model in $CODEX_HOME/models_cache.json; Codex is unavailable for this call" "$WORK/err")"
```

In `worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts`:
- In the `it.each` at lines 684-687 (`'skips empty installed tiers for %s (installed %j) -> %s'`), delete the row `['gpt-6-sol', ['gpt-6-luna'], 'gpt-6-astra'],`.
- Replace lines 713-724 (`'falls back to the built-in ladder when the models cache is unreadable: %s -> %s'`) with:

```ts
  it.each([
    ['gpt-6-luna', 'terra'],
    ['gpt-6-sol', 'astra'],
  ])('is unavailable with no pinned fallback when the models cache is unreadable: %s', async (requester, family) => {
    const h = harness();
    h.deps.readFile = vi.fn(async () => { throw enoent(); });
    h.deps.trustedRequesterModel = requester;
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: requester }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'codex-reviewer-model-unavailable',
    }));
    expect(String(payload(result).diagnostic)).toContain(`Codex model family ${family}`);
    expect(String(payload(result).diagnostic)).toContain(MODELS_CACHE_PATH);
    expect(h.spawn).toHaveBeenCalledTimes(1); // the companion preflight only; no reviewer launch
  });

  it('is unavailable when no installed model is at or above the one-up tier', async () => {
    const h = harness();
    h.deps.readFile = vi.fn(async (target: string) => {
      if (target === MODELS_CACHE_PATH) return JSON.stringify({ models: [{ slug: 'gpt-6-luna' }] });
      throw enoent();
    });
    h.deps.trustedRequesterModel = 'gpt-6-sol';
    const result = await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-6-sol' }, identity, session, runtime, h.deps,
    );
    expect(payload(result)).toEqual(expect.objectContaining({
      success: false,
      reason: 'codex-reviewer-model-unavailable',
    }));
    expect(String(payload(result).diagnostic)).toContain('Codex model family astra');
    expect(h.spawn).toHaveBeenCalledTimes(1);
  });
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_no_pinned_codex_models.py -q > /private/tmp/claude-502/taf2/t5-red-py.log 2>&1; echo "red_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t5-red-py.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf2/t5-red-hook.log 2>&1; echo "hook=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t5-red-hook.log; tail -n 1 /private/tmp/claude-502/taf2/t5-red-hook.log; cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts > /private/tmp/claude-502/taf2/t5-red-ts.log 2>&1; echo "red_ts=$?"; rg -n -e ' FAIL ' -e 'Tests  ' /private/tmp/claude-502/taf2/t5-red-ts.log
```

Expected:
- `red_py=1`. FAILED is exactly `test_no_pinned_codex_model_slugs_in_runtime_sources`; the hits are in `plan-validator.sh`, `advisor-review.ts` and `dist/index.js` only. `config/ironclaude.json.example` has no hits, because Task 4 already replaced its slugs with family words; restoring any slug there (for example `"haiku": "gpt-5.6-luna"`) makes this test fail.
- `hook=1`, with exactly these 9 FAIL lines and `Results: 67 passed, 9 failed` (76 assertions):
  1. `FAIL: no cache -> rc 1 (Codex unavailable, fail open)`
  2. `FAIL: no cache -> backend label luna:unavailable`
  3. `FAIL: no cache -> codex not called`
  4. `FAIL: no cache -> stderr names the family and the cache path`
  5. `FAIL: cache without a Luna -> backend label luna:unavailable`
  6. `FAIL: codex sonnet, no cache -> rc 1`
  7. `FAIL: codex sonnet, no cache -> backend label terra:unavailable`
  8. `FAIL: codex sonnet, no cache -> codex not called`
  9. `FAIL: codex sonnet, no cache -> stderr names the family and the cache path`
- `red_ts=1`. The summary shows 3 failed, with FAIL lines exactly:
  - `is unavailable with no pinned fallback when the models cache is unreadable: gpt-6-luna`
  - `is unavailable with no pinned fallback when the models cache is unreadable: gpt-6-sol`
  - `is unavailable when no installed model is at or above the one-up tier`

**Step 2: Hook (GREEN for the hook)**

In `worker/hooks/plan-validator.sh`, replace lines 118-132 with:

```bash
# Newest installed gpt-<version>-<word> (word: luna|terra) from the Codex models cache. There is no
# pinned fallback (design 2026-10-17 v120-completeness R2): with no model of the family listed, print
# nothing, name the family and the cache path on stderr, and return 1 so the caller treats Codex as
# unavailable for this call. Same rule as commander provider_config.newest_codex_slug and
# state-manager advisor-review.ts newestAtTier.
_newest_codex_model() {
  local word="$1" cache="${CODEX_HOME:-$HOME/.codex}/models_cache.json" slug=""
  if [ -r "$cache" ]; then
    slug=$(jq -r --arg w "$word" '[.models[]? | .slug? | select(type == "string" and test("^gpt-[0-9]+(\\.[0-9]+)*-" + $w + "$"))]
      | sort_by(capture("^gpt-(?<v>[0-9.]+)-").v | split(".") | map(tonumber)) | last // empty' "$cache" 2>/dev/null) || slug=""
  fi
  if [ -z "$slug" ]; then
    printf '%s\n' "[plan-validator] Codex model family ${word} has no installed model in ${cache}; Codex is unavailable for this call" >&2
    return 1
  fi
  echo "$slug"
}

# Haiku class on Codex: the newest Luna. Sonnet class on Codex: the newest Terra.
_newest_luna() { _newest_codex_model luna; }
_newest_terra() { _newest_codex_model terra; }
```

In `call_validation_llm`, replace the sonnet Codex arm (lines 251-253):

```bash
    if ic_is_codex; then
      terra=$(_newest_terra)
      export VALIDATION_LLM_BACKEND="terra:${terra}"
```

with:

```bash
    if ic_is_codex; then
      if ! terra=$(_newest_terra); then
        export VALIDATION_LLM_BACKEND="terra:unavailable"
        return 1
      fi
      export VALIDATION_LLM_BACKEND="terra:${terra}"
```

Replace the haiku Codex arm (lines 379-381):

```bash
  if ic_is_codex; then
    luna=$(_newest_luna)
    export VALIDATION_LLM_BACKEND="luna:${luna}"
```

with:

```bash
  if ic_is_codex; then
    if ! luna=$(_newest_luna); then
      export VALIDATION_LLM_BACKEND="luna:unavailable"
      return 1
    fi
    export VALIDATION_LLM_BACKEND="luna:${luna}"
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf2/t5-green-hook.log 2>&1; echo "hook=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t5-green-hook.log; tail -n 1 /private/tmp/claude-502/taf2/t5-green-hook.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh > /private/tmp/claude-502/taf2/t5-green-gbtw.log 2>&1; echo "gbtw=$?"; tail -n 1 /private/tmp/claude-502/taf2/t5-green-gbtw.log
```

Expected: `hook=0`, no FAIL lines, `Results: 76 passed, 0 failed`; `gbtw=0`.

**Step 3: Broker (GREEN for TS) and typecheck**

In `worker/mcp-servers/state-manager/src/tools/advisor-review.ts`, delete lines 21-22: the `// Used when Codex's models_cache.json is missing...` comment and `export const FALLBACK_CODEX_MODELS = ...`. Replace lines 227-255, `readInstalledCodexModels` and `resolveCodexReviewer`, with:

```ts
export function codexModelsCachePath(env: NodeJS.ProcessEnv): string {
  const codexHome = env.CODEX_HOME || path.join(env.HOME || os.homedir(), '.codex');
  return path.join(codexHome, 'models_cache.json');
}


// No pinned fallback (design 2026-10-17 v120-completeness R2): a missing, unreadable or malformed
// cache lists no installed model, and the review then fails with codex-reviewer-model-unavailable.
export async function readInstalledCodexModels(
  deps: Pick<AdvisorReviewDeps, 'env' | 'readFile'>,
): Promise<string[]> {
  try {
    const parsed = JSON.parse(await deps.readFile(codexModelsCachePath(deps.env), 'utf8')) as {
      models?: Array<{ slug?: unknown } | null>;
    };
    return (Array.isArray(parsed?.models) ? parsed.models : [])
      .map((entry) => entry?.slug)
      .filter((slug): slug is string => parseCodexModel(slug) !== null);
  } catch {
    return [];
  }
}


function oneUpTierIndex(requester: string): number {
  const parsed = parseCodexModel(requester);
  const ceiling = TIER_ORDER.length - 1;
  return parsed ? Math.min(TIER_ORDER.indexOf(parsed.tier) + 1, ceiling) : ceiling;
}


export function resolveCodexReviewer(requester: string, installed: readonly string[]): string | null {
  for (let index = oneUpTierIndex(requester); index < TIER_ORDER.length; index += 1) {
    const newest = newestAtTier(installed, TIER_ORDER[index]);
    if (newest) return newest;
  }
  return null;
}
```

Replace lines 617-619:

```ts
  const reviewerModel = validated.reviewTier === 'same'
    ? validated.requesterModel
    : resolveCodexReviewer(validated.requesterModel, await readInstalledCodexModels(deps));
```

with:

```ts
  let reviewerModel = validated.requesterModel;
  if (validated.reviewTier !== 'same') {
    const resolved = resolveCodexReviewer(validated.requesterModel, await readInstalledCodexModels(deps));
    if (resolved === null) {
      return failure('codex-reviewer-model-unavailable', {
        diagnostic: diagnostic(
          `Codex model family ${TIER_ORDER[oneUpTierIndex(validated.requesterModel)]} (or a higher family) `
          + `has no installed model in ${codexModelsCachePath(deps.env)}`,
        ),
      });
    }
    reviewerModel = resolved;
  }
```

`diagnostic()` keeps the last 512 characters, which is enough for this message.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts > /private/tmp/claude-502/taf2/t5-green-ts.log 2>&1; echo "green_ts=$?"; rg -n -e ' FAIL ' -e 'Tests  ' /private/tmp/claude-502/taf2/t5-green-ts.log; npx tsc --noEmit > /private/tmp/claude-502/taf2/t5-tsc.log 2>&1; echo "tsc=$?"; cat /private/tmp/claude-502/taf2/t5-tsc.log
```

Expected:
- `green_ts=0`, 0 failed, no FAIL lines; apply the load rule only to the SIGTERM-resistant process-group test.
- The tsc errors are exactly the Task 1 Step 5 baseline (`tsc=0` with an empty log if that baseline was clean), and none names `advisor-review.ts` or `advisor-review.test.ts`.

**Step 4: Rebuild the runtime bundle**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run bundle > /private/tmp/claude-502/taf2/t5-bundle.log 2>&1; echo "bundle=$?"; cat /private/tmp/claude-502/taf2/t5-bundle.log; git -C /Users/roberthyatt/Code/ironclaude diff --stat -- worker/mcp-servers/state-manager/dist/index.js; git -C /Users/roberthyatt/Code/ironclaude diff -U0 -- worker/mcp-servers/state-manager/dist/index.js; echo "fallback_hits=$(rg -c 'FALLBACK_CODEX_MODELS' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js)"; echo "unavailable_hits=$(rg -c 'codex-reviewer-model-unavailable' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js)"
```

Expected:
- `bundle=0` and 1 file changed.
- `fallback_hits=` is empty and `unavailable_hits=1`.
- The `-U0` hunks only remove `FALLBACK_CODEX_MODELS` and change `readInstalledCodexModels`, `resolveCodexReviewer`, the new `codexModelsCachePath` and `oneUpTierIndex`, and the reviewer-model selection. Any other hunk is bundle drift: STOP and report it, and do not stage the bundle.

**Step 5: Docs, the Python guard and the full state-manager suite**

In `worker/skills/advisor-fallback/SKILL.md`, replace lines 73-77 with the block below. Line 73 is the line that starts with the parenthesised `gpt-<version>-luna → ...` ladder and ends `The broker`. Line 77 is the line that ends `a same-tier blind pass. The reviewer stays a Codex model;`. Line 78 (`do not cross to a Claude model. ...`) stays.

```text
(`gpt-<version>-luna → gpt-<version>-terra → gpt-<version>-sol → gpt-<version>-astra`). The broker
resolves each tier to the newest installed model in `$CODEX_HOME/models_cache.json`. It has no pinned
fallback: with no installed model at the one-up tier or above, it returns `codex-reviewer-model-unavailable`.
With today's installed models it maps **`gpt-6-sol` → `gpt-6-astra`**. That is why a Sol session asks for
`"same"`. At the Astra ceiling it runs **`gpt-6-astra` → `gpt-6-astra`**, a same-tier blind pass. The reviewer stays a Codex model;
```

In `worker/config-schema/llm-backend.md`:
- Replace line 232 (`- **The per-client mapping.** ...`) with:

```markdown
- **The per-client mapping.** Hooks: Claude `claude --model haiku`; Codex: the newest installed `gpt-*-luna` from `$CODEX_HOME/models_cache.json`, the same resolution as the advisor broker. There is no pinned fallback: with no Luna installed, Codex is unavailable for the call and the hook fails open. Commander: the `grader` role's client order. Claude uses `models.haiku`; Codex uses `providers.clients.codex.models.haiku`, the family word `luna` by default, resolved to the same newest-installed Luna (an explicit slug is used as-is), so the hook and the Commander pick the same model.
```

- Replace table rows 249-251 with:

```markdown
| `haiku` | Claude Haiku | newest installed `gpt-*-luna`; none installed -> Codex unavailable | `providers.clients.codex.models.haiku` (default `luna`: newest installed Luna) |
| `sonnet` | Claude Sonnet | newest installed `gpt-*-terra`; none installed -> Codex unavailable | `providers.clients.codex.models.sonnet` (default `terra`: newest installed Terra) |
| `opus` | Claude Opus | not used by the hooks | `providers.clients.codex.models.opus` (default `sol`: newest installed Sol) |
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_no_pinned_codex_models.py tests/test_fast_model.py tests/test_advisor_fallback_directive.py tests/test_executing_plans_skill.py -q > /private/tmp/claude-502/taf2/t5-green-py.log 2>&1; echo "green_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t5-green-py.log; tail -n 1 /private/tmp/claude-502/taf2/t5-green-py.log; rg -n -e 'gpt-5.6' -e 'gpt-6-luna' /Users/roberthyatt/Code/ironclaude/worker/config-schema/llm-backend.md; echo "doc_rc=$?"; cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf2/t5-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf2/t5-npm.log
```

Expected:
- `green_py=0`, 0 failed. This includes `test_codex_no_tools_args_match_bundle` against the rebuilt bundle.
- `doc_rc=1`, with no matching lines.
- `npm=0`, or FAIL lines exactly the Task 1 Step 5 baseline failing set. The load rule applies only to the SIGTERM-resistant process-group test.

**Step 6: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/plan-validator.sh worker/hooks/tests/test-fast-model-fallback.sh worker/mcp-servers/state-manager/src/tools/advisor-review.ts worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts worker/mcp-servers/state-manager/dist/index.js commander/tests/test_no_pinned_codex_models.py worker/skills/advisor-fallback/SKILL.md worker/config-schema/llm-backend.md
```

Expected: no output.

---

## Task 6: Brain gate root under ~/.ironclaude/state (R3) (depends on 5)

`hooks.json` is not touched, so Codex's per-entry hook trust keys do not change: no hook group is reordered or inserted. Task 13 verifies live that the Codex Brain's hooks canary still fires after the script edits.

**Files:**
- Modify `commander/src/ironclaude/paths.py` (append).
- Modify `commander/src/ironclaude/brain_client.py`: lines 35-36 and `start` at lines 327-335.
- Modify `commander/src/ironclaude/codex_brain_client.py`:
  - the imports;
  - the `_BRAIN_GATE_ROOT` constant at line 55 (line 51 after Task 4, which removed 4 lines);
  - `_spawn_env`;
  - `_reset_brain_gate_startup_state`;
  - the canary path in `start`.
- Modify `worker/hooks/brain-gated-actions.sh`, lines 129-137.
- Modify `commander/hooks/brain-hooks-canary.sh`.
- Modify `worker/hooks/test-brain-gated-actions.sh`.
- Modify `commander/hooks/tests/test-brain-hooks-canary.sh` (rewrite).
- Create `commander/tests/test_brain_gate_root.py`.
- Modify `commander/tests/test_brain_client_gate_session.py`, `commander/tests/test_codex_brain_hooks_canary.py` and `commander/tests/test_codex_brain_client.py`.

**Step 1: Python tests (RED)**

Create `commander/tests/test_brain_gate_root.py`:

```python
"""Design 2026-10-17 v120-completeness R3: one Brain gate root, ~/.ironclaude/state/brain-gate,
created 0700 by the Commander and passed to both Brain clients and both Brain hooks."""
import os
import re
import stat
from pathlib import Path
from unittest.mock import patch

from ironclaude import paths
from ironclaude.brain_client import BrainClient
from ironclaude.codex_brain_client import CodexBrainClient

REPO = Path(__file__).resolve().parents[2]
GATE_HOOK = REPO / "worker" / "hooks" / "brain-gated-actions.sh"
CANARY_HOOK = REPO / "commander" / "hooks" / "brain-hooks-canary.sh"


def test_gate_root_is_under_ironclaude_state():
    assert paths.brain_gate_root() == Path(os.environ["HOME"]) / ".ironclaude" / "state" / "brain-gate"


def test_ensure_gate_root_creates_and_tightens_to_0700():
    root = paths.brain_gate_root()
    assert not root.exists()
    assert paths.ensure_brain_gate_root() == root
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    os.chmod(root, 0o755)
    paths.ensure_brain_gate_root()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_both_brain_clients_pass_the_same_root(monkeypatch):
    root = str(paths.brain_gate_root())
    codex = CodexBrainClient()
    assert codex._spawn_env()["IRONCLAUDE_BRAIN_GATE_ROOT"] == root
    codex._reset_brain_gate_startup_state()
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    # setenv records the originals so teardown restores them (start() mutates os.environ).
    monkeypatch.setenv("IC_ROLE", "restored-by-monkeypatch")
    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_SESSION", "restored-by-monkeypatch")
    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_ROOT", "restored-by-monkeypatch")
    with patch.object(BrainClient, "_kill_brain_subprocess"), \
         patch.object(BrainClient, "discover_episodic_memory_path", side_effect=RuntimeError("stop")):
        try:
            BrainClient().start("prompt", cwd=os.environ["HOME"])
        except RuntimeError:
            pass
    assert os.environ["IRONCLAUDE_BRAIN_GATE_ROOT"] == root


def _root_check(hook):
    body = re.search(r"^gate_root_problem\(\) \{\n(.*?)^\}", hook.read_text(), re.S | re.M)
    assert body is not None, f"{hook.name} defines no gate_root_problem"
    return body.group(1)


def test_both_brain_hooks_share_one_root_check_and_default():
    assert _root_check(GATE_HOOK) == _root_check(CANARY_HOOK)
    default = 'GATE_ROOT="${IRONCLAUDE_BRAIN_GATE_ROOT:-$HOME/.ironclaude/state/brain-gate}"'
    for hook in (GATE_HOOK, CANARY_HOOK):
        text = hook.read_text()
        assert default in text
        assert "/tmp/ic/codex-brain-gate" not in text
    for module in ("brain_client.py", "codex_brain_client.py"):
        assert "/tmp/ic/codex-brain-gate" not in (REPO / "commander" / "src" / "ironclaude" / module).read_text()
```

In `commander/tests/test_brain_client_gate_session.py` `test_start_exports_gate_session_and_resets_lookback`:
- Change the signature to `def test_start_exports_gate_session_and_resets_lookback(monkeypatch):`.
- Replace the two lines `    import ironclaude.brain_client as module` and `    monkeypatch.setattr(module, "_BRAIN_GATE_ROOT", tmp_path)` with `    from ironclaude import paths`.
- Replace `    state = tmp_path / client._brain_gate_session` and `    state.mkdir()` with `    state = paths.brain_gate_root() / client._brain_gate_session` and `    state.mkdir(parents=True)`.
- After `    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_SESSION", "restored-by-monkeypatch")`, add `    monkeypatch.setenv("IRONCLAUDE_BRAIN_GATE_ROOT", "restored-by-monkeypatch")`.
- Replace `client.start("prompt", cwd=str(tmp_path))` with `client.start("prompt", cwd=os.environ["HOME"])`.
- After the `IRONCLAUDE_BRAIN_GATE_SESSION` assertion, add `    assert os.environ["IRONCLAUDE_BRAIN_GATE_ROOT"] == str(paths.brain_gate_root())`.

In `commander/tests/test_codex_brain_hooks_canary.py`:
- Replace `    monkeypatch.setattr(module, "_BRAIN_GATE_ROOT", tmp_path)` in `_client` with `    root = paths.brain_gate_root()`.
- In `write`, replace `            marker = tmp_path / client._brain_gate_session / "hooks-live"` with `            marker = root / client._brain_gate_session / "hooks-live"`.
- Add `from ironclaude import paths` after `from unittest.mock import MagicMock`.
- In `test_stale_canary_from_earlier_start_does_not_count`, replace `    stale = tmp_path / client._brain_gate_session / "hooks-live"` with `    stale = paths.brain_gate_root() / client._brain_gate_session / "hooks-live"`, and `    stale.parent.mkdir(parents=True)` stays.

In `commander/tests/test_codex_brain_client.py` `class TestBrainGateEnvironment`:
- In `test_gate_markers_do_not_leak_into_daemon_environment`, add `        monkeypatch.delenv("IRONCLAUDE_BRAIN_GATE_ROOT", raising=False)` after the third `delenv`, and `        assert "IRONCLAUDE_BRAIN_GATE_ROOT" not in os.environ` after the last assertion.
- In `test_startup_reset_clears_lookback_only`:
  - Replace `        import ironclaude.codex_brain_client as module` and the blank line plus `        monkeypatch.setattr(module, "_BRAIN_GATE_ROOT", tmp_path)` with `        from ironclaude import paths`.
  - Replace `        state_dir = tmp_path / client._brain_gate_session` and `        state_dir.mkdir()` with `        state_dir = paths.brain_gate_root() / client._brain_gate_session` and `        state_dir.mkdir(parents=True)`.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_gate_root.py tests/test_brain_client_gate_session.py tests/test_codex_brain_hooks_canary.py tests/test_codex_brain_client.py -q > /private/tmp/claude-502/taf2/t6-red-py.log 2>&1; echo "red_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t6-red-py.log; tail -n 1 /private/tmp/claude-502/taf2/t6-red-py.log
```

Expected: `red_py=1`. FAILED is exactly these 9:
- `test_brain_gate_root.py::test_gate_root_is_under_ironclaude_state`
- `test_brain_gate_root.py::test_ensure_gate_root_creates_and_tightens_to_0700`
- `test_brain_gate_root.py::test_both_brain_clients_pass_the_same_root`
- `test_brain_gate_root.py::test_both_brain_hooks_share_one_root_check_and_default`
- `test_brain_client_gate_session.py::test_start_exports_gate_session_and_resets_lookback`
- `test_codex_brain_hooks_canary.py::test_canary_present_keeps_brain_running`
- `test_codex_brain_hooks_canary.py::test_canary_absent_stops_brain_visibly`
- `test_codex_brain_hooks_canary.py::test_stale_canary_from_earlier_start_does_not_count`
- `test_codex_brain_client.py::TestBrainGateEnvironment::test_startup_reset_clears_lookback_only`

All of them fail with `AttributeError: module 'ironclaude.paths' has no attribute 'brain_gate_root'` (`paths.brain_gate_root` does not exist yet). `test_gate_markers_do_not_leak_into_daemon_environment` passes.

**Step 2: Python (GREEN for Python)**

Append to `commander/src/ironclaude/paths.py`:

```python


def brain_gate_root() -> Path:
    """Per-Brain gate state root for brain-gated-actions.sh and brain-hooks-canary.sh (design
    2026-10-17 v120-completeness R3). Both Brain clients pass it as IRONCLAUDE_BRAIN_GATE_ROOT."""
    return _ironclaude_dir() / "state" / "brain-gate"


def ensure_brain_gate_root() -> Path:
    """Create the gate root (and parents) and force mode 0700. The Brain hooks fail closed unless
    it is a real directory owned by this user with no group or other permission bits."""
    root = brain_gate_root()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root
```

In `commander/src/ironclaude/brain_client.py`:
- Delete lines 35-36 (the comment and `_BRAIN_GATE_ROOT = Path("/tmp/ic/codex-brain-gate")`).
- In `start`, replace:

```python
        os.environ["IRONCLAUDE_BRAIN_GATE_SESSION"] = self._brain_gate_session
        for marker in ("lookback-slack", "lookback-ledger"):
            try:
                (_BRAIN_GATE_ROOT / self._brain_gate_session / marker).unlink()
```

with:

```python
        os.environ["IRONCLAUDE_BRAIN_GATE_SESSION"] = self._brain_gate_session
        # Design 2026-10-17 v120-completeness R3: one gate root for both Brains and both hooks.
        gate_root = paths.brain_gate_root()
        os.environ["IRONCLAUDE_BRAIN_GATE_ROOT"] = str(gate_root)
        try:
            paths.ensure_brain_gate_root()
        except OSError as exc:
            logger.warning("Brain gate root %s could not be prepared: %s", gate_root, exc)
        for marker in ("lookback-slack", "lookback-ledger"):
            try:
                (gate_root / self._brain_gate_session / marker).unlink()
```

In `commander/src/ironclaude/codex_brain_client.py`:
- Add `from ironclaude import paths` before `from ironclaude.config import effort_for_tier`.
- Delete the line `_BRAIN_GATE_ROOT = Path("/tmp/ic/codex-brain-gate")`.
- In `_spawn_env`, add `            "IRONCLAUDE_BRAIN_GATE_ROOT": str(paths.brain_gate_root()),` after the `"IRONCLAUDE_BRAIN_GATE_SESSION"` entry.
- In `_reset_brain_gate_startup_state`, replace `        state_dir = _BRAIN_GATE_ROOT / self._brain_gate_session` with:

```python
        try:
            paths.ensure_brain_gate_root()
        except OSError as exc:
            logger.warning("Codex Brain gate root could not be prepared: %s", exc)
        state_dir = paths.brain_gate_root() / self._brain_gate_session
```

- In `start`, replace `            canary = _BRAIN_GATE_ROOT / self._brain_gate_session / _HOOKS_CANARY_MARKER` with `            canary = paths.brain_gate_root() / self._brain_gate_session / _HOOKS_CANARY_MARKER`.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_gate_root.py tests/test_brain_client_gate_session.py tests/test_codex_brain_hooks_canary.py tests/test_codex_brain_client.py tests/test_brain_client.py tests/test_codex_brain_wiring.py tests/test_paths.py -q > /private/tmp/claude-502/taf2/t6-mid-py.log 2>&1; echo "mid_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t6-mid-py.log
```

Expected: `mid_py=1`. FAILED is exactly `test_brain_gate_root.py::test_both_brain_hooks_share_one_root_check_and_default`, because the hooks still use `/tmp/ic`.

**Step 3: Hook tests (RED)**

In `worker/hooks/test-brain-gated-actions.sh`:
- Replace lines 8-9:

```bash
STATE_DIR="/tmp/ic/codex-brain-gate/$TOKEN"
TMP_DIR=$(mktemp -d /tmp/ironclaude-codex-brain-gate-test.XXXXXX)
```

with:

```bash
TMP_DIR=$(mktemp -d /tmp/ironclaude-codex-brain-gate-test.XXXXXX)
# Gate root per design 2026-10-17 v120-completeness R3: a real 0700 directory owned by this user.
GATE_ROOT="$TMP_DIR/gate-root"
mkdir -m 700 "$GATE_ROOT"
STATE_DIR="$GATE_ROOT/$TOKEN"
```

- In `run_case`, add the line `  IRONCLAUDE_BRAIN_GATE_ROOT="$GATE_ROOT" \` directly before `  IRONCLAUDE_BRAIN_GATE_SESSION="$token" \`.
- In `run_nolog`, add the line `  IRONCLAUDE_BRAIN_GATE_ROOT="$GATE_ROOT" \` directly before `  IRONCLAUDE_BRAIN_GATE_SESSION="$TOKEN" \`.
- Directly before the line `EXPECTED_MATCHER='mcp__(plugin_ironclaude_)?episodic...'`, insert:

```bash
# ── Brain gate root (design 2026-10-17 v120-completeness R3) ──
# run_root ROOT TOOL [HOME]: one hook call with an explicit IRONCLAUDE_BRAIN_GATE_ROOT; ROOT "-" leaves it unset.
run_root() {
  local root="$1" tool_name="$2" home="${3:-$HOME}" payload
  payload=$(jq -cn --arg tool_name "$tool_name" --arg cwd "$TMP_DIR" \
    '{session_id:"codex-brain-gate-test",tool_name:$tool_name,tool_input:{"hours_back":48},cwd:$cwd}')
  if [ "$root" = "-" ]; then
    env -u IRONCLAUDE_BRAIN_GATE_ROOT HOME="$home" IC_ROLE=brain IRONCLAUDE_CLIENT=codex \
      IRONCLAUDE_BRAIN_GATE_SESSION="$TOKEN" bash "$HOOK" >"$TMP_DIR/output" 2>"$TMP_DIR/error" <<<"$payload"
  else
    HOME="$home" IC_ROLE=brain IRONCLAUDE_CLIENT=codex IRONCLAUDE_BRAIN_GATE_ROOT="$root" \
      IRONCLAUDE_BRAIN_GATE_SESSION="$TOKEN" bash "$HOOK" >"$TMP_DIR/output" 2>"$TMP_DIR/error" <<<"$payload"
  fi
}
# assert_root_refused NAME ROOT TEXT: the gate blocks (rc 2) and names why.
assert_root_refused() {
  local name="$1" root="$2" text="$3" rc
  run_root "$root" "mcp__plugin_ironclaude_orchestrator__get_operator_messages"; rc=$?
  if [ "$rc" -eq 2 ] && grep -Fq "$text" "$TMP_DIR/error"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: $name rc=$rc" >&2
    cat "$TMP_DIR/error" >&2
  fi
}

rm -rf "$STATE_DIR"
run_case "mcp__plugin_ironclaude_orchestrator__get_operator_messages" '{"hours_back":48}'
if [ -f "$STATE_DIR/lookback-slack" ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "FAIL: gate state lands under IRONCLAUDE_BRAIN_GATE_ROOT" >&2
fi

DEFAULT_HOME="$TMP_DIR/home"
mkdir -p "$DEFAULT_HOME/.ironclaude/state"
mkdir -m 700 "$DEFAULT_HOME/.ironclaude/state/brain-gate"
run_root - "mcp__plugin_ironclaude_orchestrator__get_operator_messages" "$DEFAULT_HOME"
if [ -f "$DEFAULT_HOME/.ironclaude/state/brain-gate/$TOKEN/lookback-slack" ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "FAIL: default gate root is \$HOME/.ironclaude/state/brain-gate" >&2
fi

mkdir -m 700 "$TMP_DIR/real-root"
ln -s "$TMP_DIR/real-root" "$TMP_DIR/link-root"
mkdir "$TMP_DIR/group-root"
chmod 770 "$TMP_DIR/group-root"
mkdir "$TMP_DIR/other-root"
chmod 701 "$TMP_DIR/other-root"
# The physical /tmp (macOS /private/tmp) is root-owned; the owner check refuses it before the mode check.
FOREIGN_ROOT=$(cd /tmp && pwd -P)
assert_root_refused "symlinked root blocks" "$TMP_DIR/link-root" "is a symlink"
assert_root_refused "missing root blocks" "$TMP_DIR/missing-root" "is not a directory"
assert_root_refused "foreign-owned root blocks" "$FOREIGN_ROOT" "is not owned by the current user"
assert_root_refused "group-accessible root blocks" "$TMP_DIR/group-root" "has group or other permission bits"
assert_root_refused "other-accessible root blocks" "$TMP_DIR/other-root" "has group or other permission bits"
if [ ! -e "$TMP_DIR/real-root/$TOKEN" ] && [ ! -e "$TMP_DIR/group-root/$TOKEN" ] &&
   [ ! -e "$TMP_DIR/other-root/$TOKEN" ] && [ ! -e "$FOREIGN_ROOT/$TOKEN" ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "FAIL: refused roots receive no gate state" >&2
fi
```

Replace `commander/hooks/tests/test-brain-hooks-canary.sh` entirely with:

```bash
#!/bin/bash
# brain-hooks-canary.sh writes <gate root>/<gate session>/hooks-live. The gate root is
# IRONCLAUDE_BRAIN_GATE_ROOT (default $HOME/.ironclaude/state/brain-gate) and must be a real 0700
# directory owned by this user (design 2026-10-17 v120-completeness R3); otherwise nothing is written.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/brain-hooks-canary.sh"
SESSION="canary-test-$$-$(date +%s)"
TMP_DIR=$(mktemp -d /tmp/ironclaude-canary-test.XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT
ROOT="$TMP_DIR/gate-root"
mkdir -m 700 "$ROOT"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
printf '{}' | IRONCLAUDE_BRAIN_GATE_ROOT="$ROOT" IRONCLAUDE_BRAIN_GATE_SESSION="$SESSION" bash "$HOOK"; RC=$?
[ "$RC" = "0" ] && pass "canary exits 0" || fail "canary exits 0" "rc=$RC"
[ -f "$ROOT/$SESSION/hooks-live" ] && pass "marker written under IRONCLAUDE_BRAIN_GATE_ROOT" || fail "marker written under IRONCLAUDE_BRAIN_GATE_ROOT"
mkdir -p "$TMP_DIR/home/.ironclaude/state"
mkdir -m 700 "$TMP_DIR/home/.ironclaude/state/brain-gate"
printf '{}' | env -u IRONCLAUDE_BRAIN_GATE_ROOT HOME="$TMP_DIR/home" IRONCLAUDE_BRAIN_GATE_SESSION="$SESSION" bash "$HOOK"
[ -f "$TMP_DIR/home/.ironclaude/state/brain-gate/$SESSION/hooks-live" ] && pass "default root is \$HOME/.ironclaude/state/brain-gate" || fail "default root is \$HOME/.ironclaude/state/brain-gate"
printf '{}' | IRONCLAUDE_BRAIN_GATE_ROOT="$ROOT" IRONCLAUDE_BRAIN_GATE_SESSION="../escape" bash "$HOOK"; RC=$?
[ "$RC" = "0" ] && pass "invalid session exits 0" || fail "invalid session exits 0" "rc=$RC"
[ ! -e "$ROOT/../escape" ] && pass "invalid session writes nothing" || fail "invalid session writes nothing"
# refused NAME ROOT: the canary exits 0 and writes nothing under ROOT.
refused() {
  printf '{}' | IRONCLAUDE_BRAIN_GATE_ROOT="$2" IRONCLAUDE_BRAIN_GATE_SESSION="$SESSION" bash "$HOOK"; RC=$?
  if [ "$RC" = "0" ] && [ ! -e "$2/$SESSION" ]; then pass "$1"; else fail "$1" "rc=$RC"; fi
}
mkdir -m 700 "$TMP_DIR/real"
ln -s "$TMP_DIR/real" "$TMP_DIR/link"
mkdir "$TMP_DIR/group"; chmod 770 "$TMP_DIR/group"
mkdir "$TMP_DIR/other"; chmod 701 "$TMP_DIR/other"
refused "symlinked root writes nothing" "$TMP_DIR/link"
refused "group-accessible root writes nothing" "$TMP_DIR/group"
refused "other-accessible root writes nothing" "$TMP_DIR/other"
refused "missing root writes nothing" "$TMP_DIR/missing"
refused "foreign-owned root writes nothing" "$(cd /tmp && pwd -P)"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh > /private/tmp/claude-502/taf2/t6-red-gate.log 2>&1; echo "gate=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t6-red-gate.log; bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh > /private/tmp/claude-502/taf2/t6-red-canary.log 2>&1; echo "canary=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t6-red-canary.log; tail -n 1 /private/tmp/claude-502/taf2/t6-red-canary.log
```

Expected (traced against the unchanged hooks, which still write `/tmp/ic/codex-brain-gate/<token>`):
- `gate=1`, with exactly these 8 FAIL lines (each `rc=` line is followed by the hook's stderr):
  1. `FAIL: live lock contention blocks expected rc=2 actual=0`. The test's lock now sits under the temp root, which the old hook ignores.
  2. `FAIL: gate state lands under IRONCLAUDE_BRAIN_GATE_ROOT`
  3. `FAIL: default gate root is $HOME/.ironclaude/state/brain-gate`
  4. `FAIL: symlinked root blocks rc=0`
  5. `FAIL: missing root blocks rc=0`
  6. `FAIL: foreign-owned root blocks rc=0`
  7. `FAIL: group-accessible root blocks rc=0`
  8. `FAIL: other-accessible root blocks rc=0`
- `canary=1`, with exactly 2 FAIL lines and `8 passed, 2 failed`:
  - `FAIL: marker written under IRONCLAUDE_BRAIN_GATE_ROOT`
  - `FAIL: default root is $HOME/.ironclaude/state/brain-gate`

The RED runs leave `codex-brain-gate-test-<pid>` and `canary-test-<pid>-<epoch>` directories under `/private/tmp/ic/codex-brain-gate/`. They are inside `/private/tmp` and harmless.

**Step 4: Hooks (GREEN)**

In `worker/hooks/brain-gated-actions.sh`, replace lines 137-142:

```bash
STATE_DIR="/tmp/ic/codex-brain-gate/$GATE_SESSION"
LOCK_DIR="$STATE_DIR/.lock"
if ! mkdir -p "$STATE_DIR"; then
```

through the matching `fi`, with:

```bash
# Gate root (design 2026-10-17 v120-completeness R3): the Commander creates it 0700 and passes it as
# IRONCLAUDE_BRAIN_GATE_ROOT. Fail closed unless it is a real directory owned by this user with no
# group or other permission bits. Keep gate_root_problem identical to brain-hooks-canary.sh (a
# Commander test compares them).
gate_root_problem() {
  local root="$1" perms
  if [ -L "$root" ]; then echo "is a symlink"; return 0; fi
  if [ ! -d "$root" ]; then echo "is not a directory"; return 0; fi
  if [ ! -O "$root" ]; then echo "is not owned by the current user"; return 0; fi
  perms=$(ls -ld "$root") || { echo "has unreadable permissions"; return 0; }
  if [ "${perms:4:6}" != "------" ]; then echo "has group or other permission bits"; fi
  return 0
}
GATE_ROOT="${IRONCLAUDE_BRAIN_GATE_ROOT:-$HOME/.ironclaude/state/brain-gate}"
GATE_ROOT="${GATE_ROOT%/}"
ROOT_PROBLEM=$(gate_root_problem "$GATE_ROOT")
if [ -n "$ROOT_PROBLEM" ]; then
  block_pretooluse "brain-gated-actions" \
    "Brain gate root $GATE_ROOT $ROOT_PROBLEM; the gate fails closed."
fi

STATE_DIR="$GATE_ROOT/$GATE_SESSION"
LOCK_DIR="$STATE_DIR/.lock"
if ! mkdir -p "$STATE_DIR"; then
  block_pretooluse "brain-gated-actions" \
    "Codex Brain gate state directory is unavailable."
fi
```

Replace `commander/hooks/brain-hooks-canary.sh` lines 6-11, from `GATE_SESSION="${IRONCLAUDE_BRAIN_GATE_SESSION:-}"` to the end of the file, with:

```bash
GATE_SESSION="${IRONCLAUDE_BRAIN_GATE_SESSION:-}"
case "$GATE_SESSION" in
  ""|*[!A-Za-z0-9._-]*|.*) exit 0 ;;
esac
# Gate root (design 2026-10-17 v120-completeness R3). Write nothing unless it is a real directory
# owned by this user with no group or other permission bits; the Commander then stops the Brain.
# Keep gate_root_problem identical to brain-gated-actions.sh (a Commander test compares them).
gate_root_problem() {
  local root="$1" perms
  if [ -L "$root" ]; then echo "is a symlink"; return 0; fi
  if [ ! -d "$root" ]; then echo "is not a directory"; return 0; fi
  if [ ! -O "$root" ]; then echo "is not owned by the current user"; return 0; fi
  perms=$(ls -ld "$root") || { echo "has unreadable permissions"; return 0; }
  if [ "${perms:4:6}" != "------" ]; then echo "has group or other permission bits"; fi
  return 0
}
GATE_ROOT="${IRONCLAUDE_BRAIN_GATE_ROOT:-$HOME/.ironclaude/state/brain-gate}"
GATE_ROOT="${GATE_ROOT%/}"
[ -z "$(gate_root_problem "$GATE_ROOT")" ] || exit 0
mkdir -p "$GATE_ROOT/$GATE_SESSION" && touch "$GATE_ROOT/$GATE_SESSION/hooks-live"
exit 0
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh > /private/tmp/claude-502/taf2/t6-green-gate.log 2>&1; echo "gate=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t6-green-gate.log; tail -n 1 /private/tmp/claude-502/taf2/t6-green-gate.log; bash /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh > /private/tmp/claude-502/taf2/t6-green-canary.log 2>&1; echo "canary=$?"; tail -n 1 /private/tmp/claude-502/taf2/t6-green-canary.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-hooks-json-brain-entries.sh > /private/tmp/claude-502/taf2/t6-green-entries.log 2>&1; echo "entries=$?"; git -C /Users/roberthyatt/Code/ironclaude diff --stat -- worker/hooks/hooks.json; cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_brain_gate_root.py tests/test_brain_client_gate_session.py tests/test_codex_brain_hooks_canary.py tests/test_codex_brain_client.py tests/test_brain_client.py tests/test_codex_brain_wiring.py tests/test_paths.py tests/test_daemon.py tests/test_real_path_isolation.py -q > /private/tmp/claude-502/taf2/t6-green-py.log 2>&1; echo "green_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t6-green-py.log; tail -n 1 /private/tmp/claude-502/taf2/t6-green-py.log
```

Expected:
- `gate=0`, no FAIL lines, last line `ALL BRAIN GATE TESTS PASSED`.
- `canary=0` with `10 passed, 0 failed`.
- `entries=0`, and no `hooks.json` diff.
- `green_py=0`, 0 failed.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/paths.py commander/src/ironclaude/brain_client.py commander/src/ironclaude/codex_brain_client.py worker/hooks/brain-gated-actions.sh commander/hooks/brain-hooks-canary.sh worker/hooks/test-brain-gated-actions.sh commander/hooks/tests/test-brain-hooks-canary.sh commander/tests/test_brain_gate_root.py commander/tests/test_brain_client_gate_session.py commander/tests/test_codex_brain_hooks_canary.py commander/tests/test_codex_brain_client.py
```

Expected: no output.

---

## Task 7: Commit and worktree verification, verify only (R4b) (depends on 6)

No tests required: verification only. No commit or worktree code changes. The memory files are under `/Users/roberthyatt/.claude/projects/-Users-roberthyatt-Code-ironclaude/memory/`.

**Recording.**
- Each defect is recorded under `## Commit and worktree verification (R4b)` in the findings, with:
  - the memory file;
  - the grounded code path, re-read by `grep -n` in the step;
  - the commands run and their full output;
  - a verdict: `LIVE`, `FIXED`, `UNVERIFIED-LIVE: <what would prove it>` or `UNVERIFIED-HARNESS: <error>`.
- A harness failure, meaning the fixture, hook wrapper or driver errors before reaching the code under test, is recorded as `UNVERIFIED-HARNESS` and never as FIXED.
- A defect that needs a real Codex session, the sandbox or Slack is never marked FIXED from a trace alone.

**Reproduction rules.**
- Throwaway repositories under `/private/tmp/claude-502/taf2/r4b/<n>/` only.
- Never touch the operator's checkouts.
- Never commit or push in real repositories.
- Never call `allocate` or `activate` on a real repository.

**Line drift recorded at plan time.**
- The code paths cited in `project_git_intent_mint_consume_skew.md` are now at `state-activator.sh:85-104`, `:143-144` and `:150`.
- Those in `project_codex_commit_intent_not_minted.md` are now at `state-activator.sh:74-158` and `:110-140`.

The plan-time line numbers below were grounded by a read-only subagent. The minting branch (`state-activator.sh:85-104`), the consumer channel (`workspace-manager/src/index.ts:294`), the CLI command list (`cli.ts:43`) and the guarded entrypoints (`cli.ts:403`, `hook-intent.ts:128`, `index.ts:600`) were re-verified by the plan author. Step 1 re-greps every cited line before use.

**Files:** Modify `docs/plans/2026-10-17-v120-completeness-findings.md`.

**Step 1: Ground the code paths**

```bash
grep -n -e 'HUMAN_CHANNEL=' -e 'TRIMMED_PROMPT" = "/' /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh; grep -n -e "identity.client === 'claude' ? 'claude-user-prompt'" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/index.ts; grep -n -e 'export const INTERNAL_COMMAND_NAMES' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/cli.ts; grep -n -e 'no allowed_files for this session' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/plan-scope.ts; grep -n -e 'is not the merge-base; run sync_worktree_to_target' -e 'Sync requires an active assignment' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/integration.ts; grep -n -e 'must be a non-empty string' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/index.ts; ls -l /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/index.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected:
- `HUMAN_CHANNEL=` lines at 83, 89, 94 and 100.
- The `index.ts` channel mapping at line 294.
- `INTERNAL_COMMAND_NAMES` at `cli.ts:43`.
- The plan-scope, merge-base and sync strings each found, plus the `requiredString` template ``${key} must be a non-empty string`` (`index.ts:237`, `244`). It produces `repository_path must be a non-empty string`.
- The three dist files listed.

Record every line number. Any missing string means STOP and report line drift.

**Step 2: Build the harness**

```bash
mkdir -p /private/tmp/claude-502/taf2/r4b && cat > /private/tmp/claude-502/taf2/r4b/fixture.sh <<'SH'
#!/bin/bash
# fixture.sh DIR: a fresh throwaway repo + bare remote + fake HOME whose state DB holds session S
# (professional mode on, executing, one review_passed task allowing a.txt). DIR must be under r4b/.
set -eu
R="$1"; S=33333333-3333-4333-8333-333333333333
case "$R" in /private/tmp/claude-502/taf2/r4b/?*) ;; *) echo "refusing fixture dir $R" >&2; exit 2 ;; esac
rm -rf "$R"; mkdir -p "$R/home/.claude" "$R/tmp"
git init -q --initial-branch=main "$R/repo"
git -C "$R/repo" config user.name R4B; git -C "$R/repo" config user.email r4b@example.invalid
printf 'seed\n' > "$R/repo/README.md"; git -C "$R/repo" add README.md; git -C "$R/repo" commit -q -m seed
git init -q --bare "$R/remote.git"; git -C "$R/repo" remote add origin "$R/remote.git"; git -C "$R/repo" push -q -u origin main
sqlite3 "$R/home/.claude/ironclaude.db" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE sessions(terminal_session TEXT PRIMARY KEY,professional_mode TEXT NOT NULL,workflow_stage TEXT NOT NULL,updated_at TEXT);
CREATE TABLE wave_tasks(terminal_session TEXT,status TEXT,allowed_files TEXT);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT,terminal_session TEXT NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,old_value TEXT,new_value TEXT,context TEXT);
INSERT INTO sessions VALUES('$S','on','executing',datetime('now'));
INSERT INTO wave_tasks VALUES('$S','review_passed','["a.txt"]');
SQL
echo "fixture=$R"
SH
cat > /private/tmp/claude-502/taf2/r4b/hook.sh <<'SH'
#!/bin/bash
# hook.sh DIR PROMPT CWD: run the real state-activator UserPromptSubmit hook for session S under DIR's fake HOME.
R="$1"; S=33333333-3333-4333-8333-333333333333
jq -cn --arg p "$2" --arg s "$S" --arg c "$3" '{prompt:$p,session_id:$s,cwd:$c,hook_event_name:"UserPromptSubmit",thread_source:"user"}' > "$R/p.json"
HOME="$R/home" TMPDIR="$R/tmp" IRONCLAUDE_WORKSPACE_HOOK_INTENT=/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/hook-intent.js bash /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh < "$R/p.json"
echo "hook_rc=$?"
sqlite3 "$R/home/.claude/ironclaude-workspaces.db" "SELECT 'intent', operation, human_channel, consumed_at FROM human_intents ORDER BY intent_id"
SH
cat > /private/tmp/claude-502/taf2/r4b/drive.mjs <<'JS'
// drive.mjs CLIENT TOOL ARGS_JSON (run with HOME=<DIR>/home): call one public workspace-manager tool as session S.
import { pathToFileURL } from 'node:url';
const WM = '/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist';
const [client, tool, args] = process.argv.slice(2);
const { createPublicToolDependencies, dispatchPublicTool } = await import(pathToFileURL(`${WM}/index.js`).href);
const { initCliDb } = await import(pathToFileURL(`${WM}/cli.js`).href);
const deps = createPublicToolDependencies(initCliDb(), { client, sessionId: '33333333-3333-4333-8333-333333333333', invocationThreadId: null, source: client === 'codex' ? 'codex_meta' : 'ppid_file' });
try { console.log('RESULT ' + JSON.stringify(dispatchPublicTool(tool, JSON.parse(args), deps))); }
catch (e) { console.log('ERROR ' + (e instanceof Error ? e.message : String(e))); process.exitCode = 1; }
JS
ls /private/tmp/claude-502/taf2/r4b
```

Expected: `drive.mjs fixture.sh hook.sh`.

**Step 3: Harness smoke test**

```bash
mkdir -p /private/tmp/claude-502/taf2/r4b/smoke && TMPDIR=/private/tmp/claude-502/taf2/r4b/smoke bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-git-authority-activation.sh > /private/tmp/claude-502/taf2/t7-smoke.log 2>&1; echo "smoke=$?"; tail -n 3 /private/tmp/claude-502/taf2/t7-smoke.log
```

Expected: `smoke=0`. The suite that drives `state-activator.sh` and `hook-intent.js` passes, which proves the hook-to-intent path runs on this machine. If `smoke!=0`, STOP and report; the harness is not trustworthy.

**Step 4: Defect 1, `/commit` silent partial commit (`project_git_intent_mint_consume_skew.md`; SCRATCH-REPRO)**

```bash
bash /private/tmp/claude-502/taf2/r4b/fixture.sh /private/tmp/claude-502/taf2/r4b/1 && printf 'a\n' > /private/tmp/claude-502/taf2/r4b/1/repo/a.txt && printf 'c\n' > /private/tmp/claude-502/taf2/r4b/1/repo/c.txt && git -C /private/tmp/claude-502/taf2/r4b/1/repo add a.txt c.txt && bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/1 /commit /private/tmp/claude-502/taf2/r4b/1/repo; HOME=/private/tmp/claude-502/taf2/r4b/1/home node /private/tmp/claude-502/taf2/r4b/drive.mjs claude commit '{"repository_path":"/private/tmp/claude-502/taf2/r4b/1/repo","message":"m"}'; echo "head:"; git -C /private/tmp/claude-502/taf2/r4b/1/repo show --name-only --format= HEAD; echo "still_staged:"; git -C /private/tmp/claude-502/taf2/r4b/1/repo diff --cached --name-only
```

Expected: the hook prints an `intent|commit|claude-user-prompt|` row; record which signature appears.
- **LIVE:** `RESULT {"state":"committed",...}`, `head:` lists only `a.txt`, and `still_staged:` lists `c.txt`, with no notice naming `c.txt`.
- **FIXED:** an `ERROR` naming `c.txt`, or `head:` lists both files.

**Step 5: Defect 2, Codex literal `/commit` mints an unusable intent (`project_codex_commit_intent_not_minted.md`; SCRATCH-REPRO, then OPERATOR-ASSISTED)**

```bash
bash /private/tmp/claude-502/taf2/r4b/fixture.sh /private/tmp/claude-502/taf2/r4b/2 && printf 'a\n' > /private/tmp/claude-502/taf2/r4b/2/repo/a.txt && git -C /private/tmp/claude-502/taf2/r4b/2/repo add a.txt && bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/2 /commit /private/tmp/claude-502/taf2/r4b/2/repo; HOME=/private/tmp/claude-502/taf2/r4b/2/home node /private/tmp/claude-502/taf2/r4b/drive.mjs codex commit '{"repository_path":"/private/tmp/claude-502/taf2/r4b/2/repo","message":"m"}'; bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/2 '$ironclaude:commit' /private/tmp/claude-502/taf2/r4b/2/repo; HOME=/private/tmp/claude-502/taf2/r4b/2/home node /private/tmp/claude-502/taf2/r4b/drive.mjs codex commit '{"repository_path":"/private/tmp/claude-502/taf2/r4b/2/repo","message":"m"}'
```

Expected:
- **LIVE:** the first hook mints `intent|commit|claude-user-prompt|`, and the first drive prints `ERROR Direct Git operation requires a matching human intent`. As a control, the `$ironclaude:commit` hook mints `codex-user-prompt` and the second drive prints `RESULT {"state":"committed"...}`.
- **FIXED:** the first drive prints `RESULT {"state":"committed"...}`.

The scratch run shows whether the channel mismatch is a sufficient cause. Whether the phone prompt fired the hook at all can only be proven live. Record the scratch verdict, then add to the Task 13 checklist: the operator types `/commit` in a Codex session with no staged changes, and the agent reads the newest `human_intents` row with `sqlite3 -readonly /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT operation,human_channel,consumed_at FROM human_intents ORDER BY intent_id DESC LIMIT 1"`. Until then the live half is `UNVERIFIED-LIVE: a fresh claude-user-prompt row from a Codex /commit that the Codex consumer refuses`.

**Step 6: Defect 3, `/commit <message>` mints no intent (`project_backlog_commit_with_message_no_intent.md`; SCRATCH-REPRO)**

```bash
bash /private/tmp/claude-502/taf2/r4b/fixture.sh /private/tmp/claude-502/taf2/r4b/3 && bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/3 '/commit Turn off tools' /private/tmp/claude-502/taf2/r4b/3/repo; echo "--- control"; bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/3 /commit /private/tmp/claude-502/taf2/r4b/3/repo
```

Expected:
- **LIVE:** the first hook call prints no `intent|` row and no diagnostic naming the bare `/commit` form. The control prints exactly one `intent|commit|claude-user-prompt|` row.
- **FIXED:** the first call mints an intent, or prints a diagnostic naming the bare form.

This exact-match refusal is pinned by `test-git-authority-activation.sh`, which rejects `/commit now`, so a fix must change that test.

**Step 7: Defect 4, Codex `/commit-and-push` with no assignment (`project_codex_commit_and_push_refuses_no_assignment.md`; SCRATCH-REPRO)**

```bash
bash /private/tmp/claude-502/taf2/r4b/fixture.sh /private/tmp/claude-502/taf2/r4b/4 && printf 'a\n' > /private/tmp/claude-502/taf2/r4b/4/repo/a.txt && printf 'b\n' > /private/tmp/claude-502/taf2/r4b/4/repo/b.txt && git -C /private/tmp/claude-502/taf2/r4b/4/repo add a.txt b.txt && bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/4 '$ironclaude:commit-and-push' /private/tmp/claude-502/taf2/r4b/4/repo; HOME=/private/tmp/claude-502/taf2/r4b/4/home node /private/tmp/claude-502/taf2/r4b/drive.mjs codex commit_and_push '{"repository_path":"/private/tmp/claude-502/taf2/r4b/4/repo","message":"m"}'; echo "remote:"; git -C /private/tmp/claude-502/taf2/r4b/4/remote.git show --name-only --format= main; echo "still_staged:"; git -C /private/tmp/claude-502/taf2/r4b/4/repo diff --cached --name-only; echo "--- no plan"; sqlite3 /private/tmp/claude-502/taf2/r4b/4/home/.claude/ironclaude.db "DELETE FROM wave_tasks"; bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/4 '$ironclaude:commit-and-push' /private/tmp/claude-502/taf2/r4b/4/repo; HOME=/private/tmp/claude-502/taf2/r4b/4/home node /private/tmp/claude-502/taf2/r4b/drive.mjs codex commit_and_push '{"repository_path":"/private/tmp/claude-502/taf2/r4b/4/repo","message":"m2"}'
```

Expected:
- **FIXED (no-assignment lane):** the first drive prints `RESULT {"state":"pushed",...}`, `remote:` lists only `a.txt`, and `still_staged:` lists `b.txt`.
- **LIVE:** any refusal in the first drive.
- **Record separately:** the no-plan drive's output. The expected residual gap is `ERROR ... Cannot read plan scope: no allowed_files for this session (no active plan)`. Also note that literal `/commit-and-push` typed in Codex hits defect 2.

The push goes only to the throwaway bare remote `r4b/4/remote.git`.

**Step 8: Defect 5, Codex sandbox EPERM on the worktree git index (`project_codex_sandbox_blocks_worktree_git_index.md`; OPERATOR-ASSISTED)**

```bash
grep -n -e 'worktrees' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/git-authority.ts; grep -rn -e 'writable_roots' /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/Code/ironclaude/commander/src; echo "writable_roots_rc=$?"; grep -n -e '^sandbox_mode' /Users/roberthyatt/.codex/config.toml
```

Expected:
- the `git-authority.ts` lines that place worktrees under `<primary>/.ironclaude/worktrees/`;
- `writable_roots_rc=1`, meaning no IronClaude code sets writable roots;
- the current `sandbox_mode` line from the operator's config, which is read only and never written.

Record a code-path trace. The live check goes to the Task 13 checklist as an operator step: in a Codex thread under `workspace-write` on a scratch repository with a managed worktree, the agent runs `git -C <worktree> add <file>`.
- **LIVE:** `fatal: Unable to create '<primary>/.git/worktrees/<guid>/index.lock': Operation not permitted`.
- **FIXED or mitigated:** exit 0.

Until it runs: `UNVERIFIED-LIVE: git add inside a managed worktree under a workspace-write Codex sandbox`.

**Step 9: Defect 6, shared resources hide uncommitted source (`project_shared_resources_hide_uncommitted_source.md`; SCRATCH-REPRO)**

```bash
bash /private/tmp/claude-502/taf2/r4b/fixture.sh /private/tmp/claude-502/taf2/r4b/6 && mkdir -p /private/tmp/claude-502/taf2/r4b/6/repo/tools && printf 'x = 1\n' > /private/tmp/claude-502/taf2/r4b/6/repo/tools/mod.py && HOME=/private/tmp/claude-502/taf2/r4b/6/home node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js configure-shared-resources '{"repository_path":"/private/tmp/claude-502/taf2/r4b/6/repo","entries":["tools/mod.py"]}'; echo "configure_rc=$?"; HOME=/private/tmp/claude-502/taf2/r4b/6/home node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js allocate '{"repository_path":"/private/tmp/claude-502/taf2/r4b/6/repo","owner_session_id":"33333333-3333-4333-8333-333333333333","workspace_guid":"33333333-3333-4333-8333-333333333333","integration_target":"main"}'; echo "allocate_rc=$?"; ls -la /private/tmp/claude-502/taf2/r4b/6/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333/tools/mod.py; echo "status:"; git -C /private/tmp/claude-502/taf2/r4b/6/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333 status --porcelain=v1 --untracked-files=all; grep -n -e '^/tools/mod.py$' /private/tmp/claude-502/taf2/r4b/6/repo/.git/info/exclude
```

Expected:
- **LIVE:** the configure output lists `tools/mod.py` as added, the worktree path is a symlink (`->`), `status:` is empty, and the exclude grep hits.
- **FIXED:** the entry is rejected or flagged as an untracked source file.

**Step 10: Defect 7, integration target fixed at activation (`project_reconcile_target_locked_at_activation.md`; SCRATCH-REPRO)**

```bash
bash /private/tmp/claude-502/taf2/r4b/fixture.sh /private/tmp/claude-502/taf2/r4b/7 && git -C /private/tmp/claude-502/taf2/r4b/7/repo checkout -q -b feature && HOME=/private/tmp/claude-502/taf2/r4b/7/home node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js allocate '{"repository_path":"/private/tmp/claude-502/taf2/r4b/7/repo","owner_session_id":"33333333-3333-4333-8333-333333333333","workspace_guid":"33333333-3333-4333-8333-333333333333"}'; echo "first_rc=$?"; HOME=/private/tmp/claude-502/taf2/r4b/7/home node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js allocate '{"repository_path":"/private/tmp/claude-502/taf2/r4b/7/repo","owner_session_id":"33333333-3333-4333-8333-333333333333","workspace_guid":"33333333-3333-4333-8333-333333333333","integration_target":"main"}'; echo "second_rc=$?"; grep -n -e 'into local main' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/index.ts; grep -n -e 'only the canonical' /Users/roberthyatt/Code/ironclaude/worker/skills/use-managed-worktree/SKILL.md
```

Expected:
- **LIVE:** both allocations return `"integration_target":"feature"` with rc 0, and the tool text still says `into local main`.
- **FIXED:** the second call returns `main` or refuses with an explicit target mismatch.

Also record the `use-managed-worktree/SKILL.md` line. Its instruction to pass only `repository_path` conflicts with the activate schema, which requires `integration_target`.

**Step 11: Defect 8, stale base_commit and misleading sync advice (`project_reconcile_stale_base_misleading_sync.md`; SCRATCH-REPRO)**

```bash
bash /private/tmp/claude-502/taf2/r4b/fixture.sh /private/tmp/claude-502/taf2/r4b/8 && HOME=/private/tmp/claude-502/taf2/r4b/8/home node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js allocate '{"repository_path":"/private/tmp/claude-502/taf2/r4b/8/repo","owner_session_id":"33333333-3333-4333-8333-333333333333","workspace_guid":"33333333-3333-4333-8333-333333333333","integration_target":"main"}' && printf 'w\n' > /private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333/w.txt && git -C /private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333 add w.txt && git -C /private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333 commit -q -m w && git -C /private/tmp/claude-502/taf2/r4b/8/repo merge -q --ff-only ironclaude/33333333-3333-4333-8333-333333333333; echo "ff_rc=$?"; bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/8 /reconcile /private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333; HOME=/private/tmp/claude-502/taf2/r4b/8/home node /private/tmp/claude-502/taf2/r4b/drive.mjs claude reconcile_worktree '{"repository_path":"/private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333","workspace_guid":"33333333-3333-4333-8333-333333333333"}'; HOME=/private/tmp/claude-502/taf2/r4b/8/home node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/dist/cli.js sync '{"repository_path":"/private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333","workspace_guid":"33333333-3333-4333-8333-333333333333","owner_session_id":"33333333-3333-4333-8333-333333333333"}'; echo "sync_rc=$?"; bash /private/tmp/claude-502/taf2/r4b/hook.sh /private/tmp/claude-502/taf2/r4b/8 /reconcile /private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333; HOME=/private/tmp/claude-502/taf2/r4b/8/home node /private/tmp/claude-502/taf2/r4b/drive.mjs claude reconcile_worktree '{"repository_path":"/private/tmp/claude-502/taf2/r4b/8/repo/.ironclaude/worktrees/33333333-3333-4333-8333-333333333333","workspace_guid":"33333333-3333-4333-8333-333333333333"}'
```

Expected: `ff_rc=0`.
- **LIVE:**
  1. The first reconcile prints `ERROR ... Finalization base_commit is not the merge-base; run sync_worktree_to_target`.
  2. The sync prints `Sync requires an active assignment; preserving worktree`, or a no-op.
  3. The second reconcile prints `RESULT {"state":"reconciled"...}`.
- **FIXED:** the first reconcile succeeds; or the sync updates the base; or the error names the recovery that works.

If the worktree branch name differs from `ironclaude/<guid>`, read it with `git -C <worktree> branch --show-current` and record it; that is a harness adjustment, not a verdict.

**Step 12: Defect 9, activation calls `list_active_assignments` without `repository_path` (`project_activate_list_assignments_empty_repo_path.md`; READ-ONLY-TRACE, then OPERATOR-ASSISTED)**

```bash
grep -n -e 'repository_path' -e 'show-toplevel' -e 'list_active_assignments' /Users/roberthyatt/Code/ironclaude/worker/skills/activate-professional-mode/SKILL.md; grep -n -e "case 'list_active_assignments'" -e 'must be a non-empty string' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/index.ts
```

Expected: record every line.
- **LIVE (skill text):** the skill names `list_active_assignments` but gives no instruction to resolve or pass `repository_path`, for example via `git rev-parse --show-toplevel`.
- **FIXED:** the skill names how to resolve and pass it.

The live half goes to the Task 13 checklist: the operator runs `/activate-professional-mode` in a fresh session, and the agent greps that session's transcript for a first `list_active_assignments` call without `repository_path`. Until then: `UNVERIFIED-LIVE: an errored first list_active_assignments call in a fresh activation transcript`.

**Step 13: Record and stage**

Append `## Commit and worktree verification (R4b)` to the findings. It holds:
- a table with one row per defect: memory file, method, verdict and one-line evidence;
- the full Step 1-12 outputs;
- the operator-assisted items carried to Task 13 (defects 2, 5 and 9).

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-v120-completeness-findings.md; git -C /Users/roberthyatt/Code/ironclaude status --porcelain=v1 -- worker/mcp-servers/workspace-manager worker/hooks/state-activator.sh worker/skills
```

Expected: the status lists only `worker/skills/advisor-fallback/SKILL.md` and `worker/skills/executing-plans/SKILL.md`, both staged as `M ` from Tasks 2 and 5. There is no workspace-manager or `state-activator.sh` entry, which proves this task changed no commit or worktree code.

---

## Task 8: Local-box backoff in the hook (R4c, Part F) (depends on 7)

**Files:**
- Modify `worker/hooks/plan-validator.sh`:
  - new helpers after `_resolve_budgets` (which ends at line 86);
  - in `call_validation_llm`, a skip check after the line `  [ -n "$hook_budget" ] || hook_budget=8` and a record step directly before the comment line `  # R1 chain. A usable local answer is valid JSON (jq -e .). Empty, failed or non-JSON`.
- Create `worker/hooks/tests/test-local-backoff.sh`.
- Modify four existing suites that drive `call_validation_llm` against a stub local box several times per run: `worker/hooks/tests/test-fast-model-fallback.sh` (after line 6), `worker/hooks/tests/test-openai-backend.sh` (after line 5), `worker/hooks/tests/test-last-assistant-text.sh` (after line 6) and `worker/hooks/tests/test-gbtw-continuation-judge.sh` (after line 3). Each gets one line that turns the backoff off, so each call still exercises its own chain and nothing is written under the real `~/.ironclaude`. `test-fast-model-fallback.sh` and `test-openai-backend.sh` do not override `HOME`; and `test-fast-model-fallback.sh` lines 112-128 fail the `http://h/v1` box and then expect the same box's JSON answer.

**Step 1: Hook test (RED)**

In each of the four existing suites, insert this line at the position given above:

```bash
export IRONCLAUDE_LOCAL_BACKOFF_DIR=/dev/null  # local-box backoff off (not a directory): each call tests its own chain; nothing touches the real ~/.ironclaude
```

Create `worker/hooks/tests/test-local-backoff.sh`:

```bash
#!/bin/bash
# test-local-backoff.sh — the local-box backoff in plan-validator.sh call_validation_llm (design
# 2026-10-17 v120-completeness R4c): a failed local-box attempt skips the box for
# local_backoff_minutes (default 15, 0 off); expiry retries; success clears; the marker directory
# must be a real 0700 directory owned by this user. A stub curl counts box calls. Do NOT set -e.
PASS=0; FAIL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/../hook-logger.sh"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/../plan-validator.sh"

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then echo "PASS: $desc"; ((PASS++));
  else echo "FAIL: $desc"; echo "  expected: $expected"; echo "  actual:   $actual"; ((FAIL++)); fi
}

WORK=$(mktemp -d)
STUBBIN="$WORK/bin"; mkdir -p "$STUBBIN"
export STUB_LOG_DIR="$WORK/log"; mkdir -p "$STUB_LOG_DIR"
cat > "$STUBBIN/curl" <<'EOF'
#!/bin/bash
echo called >> "$STUB_LOG_DIR/curl.calls"
case "${STUB_CURL_MODE:-fail}" in
  fail) exit 7 ;;
  ok)   printf '%s' '{"choices":[{"message":{"content":"{\"ok\":true,\"src\":\"local\"}"}}]}' ;;
esac
EOF
cat > "$STUBBIN/claude" <<'EOF'
#!/bin/bash
cat > /dev/null
printf '%s' '[{"type":"result","subtype":"success","structured_output":{"ok":true,"src":"haiku"}}]'
EOF
chmod +x "$STUBBIN/curl" "$STUBBIN/claude"
export PATH="$STUBBIN:$PATH" IC_HOOK_CLIENT=claude STUB_CURL_MODE=fail
mkdir -p "$WORK/state"
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$WORK/state/local-backoff"
MARKER="$IRONCLAUDE_LOCAL_BACKOFF_DIR/local-box-backoff.json"
SCHEMA='{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}'
OPENAI='{"backend":"openai","openai":{"base_url":"http://box.invalid/v1","model":"m"}}'
LOCAL='{"ok":true,"src":"local"}'
HAIKU='{"ok":true,"src":"haiku"}'
cfg() { printf '%s' "$1" > "$WORK/cfg.json"; export IC_OLLAMA_CONFIG_PATH="$WORK/cfg.json"; }
run() { rm -f "$STUB_LOG_DIR/curl.calls"; call_validation_llm "grade this" "$SCHEMA" > "$WORK/out"; OUT=$(jq -c . "$WORK/out" 2>/dev/null); }
box_calls() { if [ -f "$STUB_LOG_DIR/curl.calls" ]; then wc -l < "$STUB_LOG_DIR/curl.calls" | tr -d ' '; else echo 0; fi; }
window() { echo $(( $(jq -r '.until' "$1" 2>/dev/null) - $(date +%s) )); }
in_range() { local w; w=$(window "$1"); [ "$w" -ge "$2" ] && [ "$w" -le "$3" ] && echo yes || echo no; }
live_marker() { printf '{"endpoint":"%s","until":%s}' "$2" "$(( $(date +%s) + $3 ))" > "$1"; }

echo "=== no marker: the box is tried (positive control) and its failure writes the marker ==="
cfg "$OPENAI"; run
assert_eq "no marker -> box tried once" "1" "$(box_calls)"
assert_eq "box down -> haiku answers" "$HAIKU" "$OUT"
assert_eq "failure writes the marker for the endpoint" "http://box.invalid/v1" "$(jq -r .endpoint "$MARKER" 2>/dev/null)"
assert_eq "marker expires in 15 minutes by default" "yes" "$(in_range "$MARKER" 890 900)"
assert_eq "marker directory is 0700" "drwx------" "$(ls -ld "$IRONCLAUDE_LOCAL_BACKOFF_DIR" 2>/dev/null | cut -c1-10)"

echo "=== live marker: the box is skipped ==="
run
assert_eq "live marker -> box not called" "0" "$(box_calls)"
assert_eq "live marker -> haiku answers" "$HAIKU" "$OUT"

echo "=== another endpoint is tried, and its success leaves this endpoint's marker ==="
cfg '{"backend":"openai","openai":{"base_url":"http://other.invalid/v1","model":"m"}}'
STUB_CURL_MODE=ok; run
assert_eq "other endpoint -> box tried" "1" "$(box_calls)"
assert_eq "other endpoint success keeps box.invalid's marker" "http://box.invalid/v1" "$(jq -r .endpoint "$MARKER" 2>/dev/null)"
cfg "$OPENAI"

echo "=== expiry: the next call tries the box and a failure renews the marker ==="
live_marker "$MARKER" "http://box.invalid/v1" -1
STUB_CURL_MODE=fail; run
assert_eq "expired marker -> box tried" "1" "$(box_calls)"
assert_eq "failure renews the marker" "yes" "$(in_range "$MARKER" 890 900)"

echo "=== success clears the marker ==="
live_marker "$MARKER" "http://box.invalid/v1" -1
STUB_CURL_MODE=ok; run
assert_eq "success -> local answer" "$LOCAL" "$OUT"
assert_eq "success clears the marker" "absent" "$([ -e "$MARKER" ] && echo present || echo absent)"
STUB_CURL_MODE=fail

echo "=== the per-backend value overrides the top-level value ==="
cfg '{"backend":"openai","local_backoff_minutes":30,"openai":{"base_url":"http://box.invalid/v1","model":"m","local_backoff_minutes":2}}'
run
assert_eq "openai.local_backoff_minutes=2 -> 2-minute marker" "yes" "$(in_range "$MARKER" 110 120)"
rm -f "$MARKER"

echo "=== 0 disables the backoff ==="
cfg '{"backend":"openai","local_backoff_minutes":0,"openai":{"base_url":"http://box.invalid/v1","model":"m"}}'
run
assert_eq "0 -> a failure writes no marker" "absent" "$([ -e "$MARKER" ] && echo present || echo absent)"
live_marker "$MARKER" "http://box.invalid/v1" 600
run
assert_eq "0 -> a live marker is ignored and the box is tried" "1" "$(box_calls)"
rm -f "$MARKER"

echo "=== the ollama arm ==="
cfg '{"backend":"ollama","ollama":{"url":"http://ollama.invalid:11434/","model":"m"}}'
run
assert_eq "ollama failure marks the url without its trailing slash" "http://ollama.invalid:11434" "$(jq -r .endpoint "$MARKER" 2>/dev/null)"
run
assert_eq "ollama live marker -> box not called" "0" "$(box_calls)"
rm -f "$MARKER"

echo "=== the marker directory must be a real 0700 directory owned by this user ==="
cfg "$OPENAI"
mkdir "$WORK/open"; chmod 770 "$WORK/open"
live_marker "$WORK/open/local-box-backoff.json" "http://box.invalid/v1" 600
BEFORE=$(cat "$WORK/open/local-box-backoff.json")
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$WORK/open"; run
assert_eq "group-accessible directory -> marker ignored, box tried" "1" "$(box_calls)"
assert_eq "group-accessible directory -> marker not rewritten" "$BEFORE" "$(cat "$WORK/open/local-box-backoff.json")"
mkdir -m 700 "$WORK/real"; ln -s "$WORK/real" "$WORK/link"
live_marker "$WORK/real/local-box-backoff.json" "http://box.invalid/v1" 600
BEFORE=$(cat "$WORK/real/local-box-backoff.json")
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$WORK/link"; run
assert_eq "symlinked directory -> marker ignored, box tried" "1" "$(box_calls)"
assert_eq "symlinked directory -> marker not rewritten" "$BEFORE" "$(cat "$WORK/real/local-box-backoff.json")"
FOREIGN=$(cd /tmp && pwd -P)
FOREIGN_BEFORE=$([ -e "$FOREIGN/local-box-backoff.json" ] && echo present || echo absent)
export IRONCLAUDE_LOCAL_BACKOFF_DIR="$FOREIGN"; run
assert_eq "foreign-owned directory -> box tried" "1" "$(box_calls)"
assert_eq "foreign-owned directory -> nothing written" "$FOREIGN_BEFORE" "$([ -e "$FOREIGN/local-box-backoff.json" ] && echo present || echo absent)"

echo "=== the default directory is \$HOME/.ironclaude/state/local-backoff ==="
unset IRONCLAUDE_LOCAL_BACKOFF_DIR
OLD_HOME="$HOME"; export HOME="$WORK/home"; mkdir -p "$HOME"
run
assert_eq "default marker path" "http://box.invalid/v1" "$(jq -r .endpoint "$HOME/.ironclaude/state/local-backoff/local-box-backoff.json" 2>/dev/null)"
export HOME="$OLD_HOME"

rm -rf "$WORK"
unset IC_OLLAMA_CONFIG_PATH IC_HOOK_CLIENT STUB_CURL_MODE
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-local-backoff.sh > /private/tmp/claude-502/taf2/t8-red.log 2>&1; echo "rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t8-red.log; tail -n 1 /private/tmp/claude-502/taf2/t8-red.log
```

Expected: `rc=1` and `Results: 15 passed, 10 failed` (25 assertions). This is traced against the unchanged hook, which always tries the box and never writes a marker. The FAIL lines are exactly these 10:
1. `FAIL: failure writes the marker for the endpoint`
2. `FAIL: marker expires in 15 minutes by default`
3. `FAIL: marker directory is 0700`
4. `FAIL: live marker -> box not called`
5. `FAIL: other endpoint success keeps box.invalid's marker`
6. `FAIL: failure renews the marker`
7. `FAIL: openai.local_backoff_minutes=2 -> 2-minute marker`
8. `FAIL: ollama failure marks the url without its trailing slash`
9. `FAIL: ollama live marker -> box not called`
10. `FAIL: default marker path`

The `live_marker` writes into the not-yet-created directory fail harmlessly on stderr.

**Step 2: Hook (GREEN)**

In `worker/hooks/plan-validator.sh`, directly after the closing `}` of `_resolve_budgets` (line 86), add:

```bash

# =============================================================================
# LOCAL-BOX BACKOFF (design 2026-10-17 v120-completeness R4c; see llm-backend.md)
# =============================================================================

# One marker, shared with commander ironclaude/local_backoff.py (a Commander test compares the
# default, the directory, the marker name and the directory check). After a failed local-box attempt
# the box is skipped for local_backoff_minutes (default 15; 0 disables); the next call after expiry
# tries it again, a success clears the marker and a failure renews it.
IC_LOCAL_BACKOFF_DEFAULT_MINUTES=15
IC_LOCAL_BACKOFF_MARKER_NAME="local-box-backoff.json"

_local_backoff_dir() {
  local d="${IRONCLAUDE_LOCAL_BACKOFF_DIR:-$HOME/.ironclaude/state/local-backoff}"
  echo "${d%/}"
}

# Same check as brain-gated-actions.sh gate_root_problem: a real directory owned by this user with no
# group or other permission bits, else the marker is neither trusted nor written.
_local_backoff_dir_problem() {
  local root="$1" perms
  if [ -L "$root" ]; then echo "is a symlink"; return 0; fi
  if [ ! -d "$root" ]; then echo "is not a directory"; return 0; fi
  if [ ! -O "$root" ]; then echo "is not owned by the current user"; return 0; fi
  perms=$(ls -ld "$root") || { echo "has unreadable permissions"; return 0; }
  if [ "${perms:4:6}" != "------" ]; then echo "has group or other permission bits"; fi
  return 0
}

# Pure helper: backoff minutes from config JSON content ($1) for backend $2, block-then-top like
# _resolve_budgets; a non-number or a negative value means the default.
_resolve_local_backoff_minutes() {
  local m
  m=$(printf '%s' "$1" | jq -r --arg b "$2" --argjson d "$IC_LOCAL_BACKOFF_DEFAULT_MINUTES" \
    '(.[$b].local_backoff_minutes // .local_backoff_minutes // $d) | if type == "number" and . >= 0 then floor else $d end' 2>/dev/null) || m=""
  [[ "$m" =~ ^[0-9]+$ ]] || m="$IC_LOCAL_BACKOFF_DEFAULT_MINUTES"
  echo "$m"
}

# Returns 0 while a live marker for endpoint $1 exists and the backoff ($2 minutes) is on.
_local_backoff_active() {
  local dir file
  [ -n "$1" ] && [ "${2:-0}" -gt 0 ] || return 1
  dir=$(_local_backoff_dir)
  [ -z "$(_local_backoff_dir_problem "$dir")" ] || return 1
  file="$dir/$IC_LOCAL_BACKOFF_MARKER_NAME"
  [ -f "$file" ] || return 1
  jq -e --arg u "$1" --argjson now "$(date +%s)" \
    '.endpoint == $u and (.until | type) == "number" and .until > $now' "$file" >/dev/null 2>&1
}

# Record a local-box attempt for endpoint $1 ($2 minutes, $3 ok|fail). ok clears a marker for this
# endpoint; fail writes {endpoint, until=now+minutes} atomically when the backoff is on.
_local_backoff_record() {
  local dir file tmp
  [ -n "$1" ] || return 0
  dir=$(_local_backoff_dir)
  file="$dir/$IC_LOCAL_BACKOFF_MARKER_NAME"
  if [ "$3" = "ok" ]; then
    [ -z "$(_local_backoff_dir_problem "$dir")" ] || return 0
    [ -f "$file" ] && jq -e --arg u "$1" '.endpoint == $u' "$file" >/dev/null 2>&1 && rm -f "$file"
    return 0
  fi
  [ "${2:-0}" -gt 0 ] || return 0
  mkdir -p "$(dirname "$dir")" 2>/dev/null || return 0
  [ -e "$dir" ] || [ -L "$dir" ] || mkdir -m 700 "$dir" 2>/dev/null || return 0
  [ -z "$(_local_backoff_dir_problem "$dir")" ] || return 0
  tmp=$(mktemp "$dir/.marker.XXXXXX") || return 0
  jq -cn --arg u "$1" --argjson until "$(( $(date +%s) + $2 * 60 ))" '{endpoint:$u,until:$until}' > "$tmp" \
    && mv -f "$tmp" "$file"
  rm -f "$tmp"
  return 0
}
```

In `call_validation_llm`, directly after the line `  [ -n "$hook_budget" ] || hook_budget=8`, add:

```bash

  # Local-box backoff (R4c): skip a box that failed within local_backoff_minutes.
  local local_url="" backoff_min=0
  case "$backend" in
    ollama) local_url=$(jq -r '.ollama.url // "http://localhost:11434"' "$config" 2>/dev/null) || local_url="" ;;
    openai) local_url=$(jq -r '.openai.base_url // empty' "$config" 2>/dev/null) || local_url="" ;;
  esac
  local_url="${local_url%/}"
  if [ -n "$local_url" ]; then
    backoff_min=$(_resolve_local_backoff_minutes "$(cat "$config" 2>/dev/null)" "$backend")
    if _local_backoff_active "$local_url" "$backoff_min"; then
      backend="backed-off"   # the case below makes no local call; the fast model answers
    fi
  fi
```

Directly before the comment line `  # R1 chain. A usable local answer is valid JSON (jq -e .). Empty, failed or non-JSON`, add:

```bash
  if [ -n "$local_url" ] && [ "$backend" != "backed-off" ]; then
    if [ -n "$result" ] && printf '%s' "$result" | jq -e . >/dev/null 2>&1; then
      _local_backoff_record "$local_url" "$backoff_min" ok
    else
      _local_backoff_record "$local_url" "$backoff_min" fail
    fi
  fi

```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-local-backoff.sh > /private/tmp/claude-502/taf2/t8-green.log 2>&1; echo "rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t8-green.log; tail -n 1 /private/tmp/claude-502/taf2/t8-green.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf2/t8-green-fast.log 2>&1; echo "fast=$?"; tail -n 1 /private/tmp/claude-502/taf2/t8-green-fast.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-openai-backend.sh > /private/tmp/claude-502/taf2/t8-green-openai.log 2>&1; echo "openai=$?"; tail -n 1 /private/tmp/claude-502/taf2/t8-green-openai.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-judge.sh > /private/tmp/claude-502/taf2/t8-green-gbtw.log 2>&1; echo "gbtw=$?"; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh > /private/tmp/claude-502/taf2/t8-green-lat.log 2>&1; echo "lat=$?"; ls /Users/roberthyatt/.ironclaude/state/local-backoff; echo "real_dir_rc=$?"
```

Expected:
- `rc=0`, no FAIL lines, `Results: 25 passed, 0 failed`.
- `fast=0` with `Results: 76 passed, 0 failed`.
- `openai=0`, `gbtw=0` and `lat=0`.
- `real_dir_rc=1`, meaning no suite wrote under the real `~/.ironclaude/state/local-backoff`. If that directory existed before this task, which the operator's live hooks may have created after a deploy, record its listing instead and confirm that no marker names a test endpoint (`box.invalid`, `h/v1`, `x`).

If a suite that calls the box twice in one run now fails because the second call is skipped, STOP and report it; do not weaken the backoff.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/plan-validator.sh worker/hooks/tests/test-local-backoff.sh worker/hooks/tests/test-fast-model-fallback.sh worker/hooks/tests/test-openai-backend.sh worker/hooks/tests/test-last-assistant-text.sh worker/hooks/tests/test-gbtw-continuation-judge.sh
```

Expected: no output.

---

## Task 9: Local-box backoff in the Commander, one marker with the hook (R4c, Part F) (depends on 8)

**Files:**
- Create `commander/src/ironclaude/local_backoff.py`.
- Modify `commander/src/ironclaude/grader.py`: the imports and `LocalGrader.grade`, lines 238-248.
- Create `commander/tests/test_local_backoff.py`.
- Modify `worker/config-schema/llm-backend.md`: add one bullet after line 231 (`- **The order.** ...`).

**Step 1: Tests (RED)**

Create `commander/tests/test_local_backoff.py`. Each import sits inside its test, so a missing module fails each test rather than collection.

```python
"""Design 2026-10-17 v120-completeness R4c: after a failed local-box attempt the box is skipped for
local_backoff_minutes (default 15, 0 off). The hooks and the Commander share one marker."""
import json
import os
import stat
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / "worker" / "hooks"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
LOCALHOST = "http://localhost:11434"


def _fast_answer(monkeypatch, obj):
    from ironclaude import fast_model

    def fake(argv, *, input_text, timeout, env):
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"type": "result", "structured_output": obj}]), "")
    monkeypatch.setattr(fast_model, "_run_child", fake)


def _hook(*args):
    script = 'source "$1/hook-logger.sh"; source "$1/plan-validator.sh"; shift; "$@"'
    return subprocess.run(["bash", "-c", script, "probe", str(HOOKS), *args],
                          capture_output=True, text=True, timeout=30)


def test_default_minutes_directory_marker_name_and_endpoints(monkeypatch, tmp_path):
    from ironclaude import local_backoff
    assert local_backoff.DEFAULT_MINUTES == 15
    assert local_backoff.MARKER_NAME == "local-box-backoff.json"
    assert local_backoff.backoff_dir() == Path(os.environ["HOME"]) / ".ironclaude" / "state" / "local-backoff"
    monkeypatch.setenv("IRONCLAUDE_LOCAL_BACKOFF_DIR", str(tmp_path / "custom") + "/")
    assert local_backoff.backoff_dir() == tmp_path / "custom"
    assert local_backoff.endpoint_for("ollama", {}) == LOCALHOST
    assert local_backoff.endpoint_for("ollama", {"url": "http://h:1/"}) == "http://h:1"
    assert local_backoff.endpoint_for("openai", {"base_url": "http://b/v1/"}) == "http://b/v1"
    assert local_backoff.endpoint_for("openai", {}) is None
    assert local_backoff.endpoint_for("haiku", {}) is None


def test_backoff_minutes_resolves_block_then_top_level():
    from ironclaude.local_backoff import backoff_minutes
    assert backoff_minutes({}, "openai") == 15
    assert backoff_minutes({"local_backoff_minutes": 30}, "openai") == 30
    both = {"local_backoff_minutes": 30, "openai": {"local_backoff_minutes": 2}}
    assert backoff_minutes(both, "openai") == 2
    assert backoff_minutes(both, "ollama") == 30
    assert backoff_minutes({"local_backoff_minutes": 0}, "ollama") == 0
    assert backoff_minutes({"local_backoff_minutes": 2.7}, "ollama") == 2
    for bad in ("x", -1, True, None, [5]):
        assert backoff_minutes({"local_backoff_minutes": bad}, "ollama") == 15, bad


def test_failure_marks_the_endpoint_until_now_plus_minutes_and_expiry_retries():
    from ironclaude import local_backoff
    now = 1_000_000.0
    local_backoff.record_failure("http://box.invalid/v1", 15, now=now)
    directory = local_backoff.backoff_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    marker = json.loads((directory / local_backoff.MARKER_NAME).read_text())
    assert marker == {"endpoint": "http://box.invalid/v1", "until": int(now) + 900}
    assert local_backoff.is_backed_off("http://box.invalid/v1", 15, now=now + 899)
    assert not local_backoff.is_backed_off("http://box.invalid/v1", 15, now=now + 900)  # expired
    assert not local_backoff.is_backed_off("http://other.invalid/v1", 15, now=now)


def test_success_clears_only_the_matching_endpoint():
    from ironclaude import local_backoff
    local_backoff.record_failure("http://a.invalid", 15)
    local_backoff.record_success("http://b.invalid")
    assert local_backoff.is_backed_off("http://a.invalid", 15)
    local_backoff.record_success("http://a.invalid")
    assert not (local_backoff.backoff_dir() / local_backoff.MARKER_NAME).exists()


def test_zero_minutes_disables_the_backoff():
    from ironclaude import local_backoff
    local_backoff.record_failure("http://a.invalid", 0)
    assert not (local_backoff.backoff_dir() / local_backoff.MARKER_NAME).exists()
    local_backoff.record_failure("http://a.invalid", 15)
    assert not local_backoff.is_backed_off("http://a.invalid", 0)


def test_marker_refuses_an_unsafe_directory(monkeypatch, tmp_path):
    from ironclaude import local_backoff
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    os.chmod(open_dir, 0o770)
    live = json.dumps({"endpoint": "http://x.invalid", "until": int(time.time()) + 600})
    (open_dir / local_backoff.MARKER_NAME).write_text(live)
    monkeypatch.setenv("IRONCLAUDE_LOCAL_BACKOFF_DIR", str(open_dir))
    assert local_backoff.dir_problem(open_dir) == "has group or other permission bits"
    assert not local_backoff.is_backed_off("http://x.invalid", 15)
    local_backoff.record_failure("http://y.invalid", 15)
    assert (open_dir / local_backoff.MARKER_NAME).read_text() == live  # not rewritten
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(real)
    assert local_backoff.dir_problem(tmp_path / "link") == "is a symlink"
    assert local_backoff.dir_problem(tmp_path / "missing") == "is not a directory"
    assert local_backoff.dir_problem(Path(os.path.realpath("/tmp"))) == "is not owned by the current user"


def test_local_grader_skips_a_backed_off_box_then_retries_after_expiry(monkeypatch):
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import OllamaConnectionError
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.side_effect = OllamaConnectionError("refused")
    assert grader.grade("s", "u", SCHEMA) == {"ok": True, "src": "fast"}
    assert grader._client.post_generate.call_count == 1
    assert local_backoff.is_backed_off(LOCALHOST, 15)
    assert grader.grade("s", "u", SCHEMA) == {"ok": True, "src": "fast"}
    assert grader._client.post_generate.call_count == 1  # skipped while backed off
    marker = local_backoff.backoff_dir() / local_backoff.MARKER_NAME
    data = json.loads(marker.read_text())
    data["until"] = int(time.time()) - 1
    marker.write_text(json.dumps(data))
    grader.grade("s", "u", SCHEMA)
    assert grader._client.post_generate.call_count == 2  # expired: tried again
    assert local_backoff.is_backed_off(LOCALHOST, 15)  # and the failure renewed it


def test_local_grader_success_clears_the_marker(monkeypatch):
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    _fast_answer(monkeypatch, {"ok": False, "src": "fast"})
    local_backoff.record_failure(LOCALHOST, 15, now=time.time() - 3600)  # expired marker
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.return_value = '{"ok": true}'
    assert grader.grade("s", "u", SCHEMA) == {"ok": True}
    assert not (local_backoff.backoff_dir() / local_backoff.MARKER_NAME).exists()


def test_hook_and_commander_share_one_marker_both_ways():
    from ironclaude import local_backoff
    assert _hook("_local_backoff_record", "http://hook.invalid/v1", "15", "fail").returncode == 0
    assert local_backoff.is_backed_off("http://hook.invalid/v1", 15)  # Commander honors the hook's marker
    local_backoff.record_failure("http://cmd.invalid:11434", 15)
    assert _hook("_local_backoff_active", "http://cmd.invalid:11434", "15").returncode == 0  # and vice versa
    assert _hook("_local_backoff_active", "http://hook.invalid/v1", "15").returncode == 1  # one marker
    local_backoff.record_success("http://cmd.invalid:11434")
    assert _hook("_local_backoff_active", "http://cmd.invalid:11434", "15").returncode == 1


def test_hook_and_commander_share_the_default_directory_and_check():
    import re
    from ironclaude import local_backoff
    text = (HOOKS / "plan-validator.sh").read_text()
    assert f"IC_LOCAL_BACKOFF_DEFAULT_MINUTES={local_backoff.DEFAULT_MINUTES}" in text
    assert f'IC_LOCAL_BACKOFF_MARKER_NAME="{local_backoff.MARKER_NAME}"' in text
    assert "${IRONCLAUDE_LOCAL_BACKOFF_DIR:-$HOME/.ironclaude/state/local-backoff}" in text
    check = re.search(r"^_local_backoff_dir_problem\(\) \{\n(.*?)^\}", text, re.S | re.M)
    gate = re.search(r"^gate_root_problem\(\) \{\n(.*?)^\}",
                     (HOOKS / "brain-gated-actions.sh").read_text(), re.S | re.M)
    assert check is not None and gate is not None and check.group(1) == gate.group(1)
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_local_backoff.py -q > /private/tmp/claude-502/taf2/t9-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t9-red.log; tail -n 1 /private/tmp/claude-502/taf2/t9-red.log
```

Expected: `red=1`. FAILED is exactly these 10, each with `ModuleNotFoundError: No module named 'ironclaude.local_backoff'`:
- `test_default_minutes_directory_marker_name_and_endpoints`
- `test_backoff_minutes_resolves_block_then_top_level`
- `test_failure_marks_the_endpoint_until_now_plus_minutes_and_expiry_retries`
- `test_success_clears_only_the_matching_endpoint`
- `test_zero_minutes_disables_the_backoff`
- `test_marker_refuses_an_unsafe_directory`
- `test_local_grader_skips_a_backed_off_box_then_retries_after_expiry`
- `test_local_grader_success_clears_the_marker`
- `test_hook_and_commander_share_one_marker_both_ways`
- `test_hook_and_commander_share_the_default_directory_and_check`

**Step 2: Module, grader and docs (GREEN)**

Create `commander/src/ironclaude/local_backoff.py`:

```python
"""Local-box backoff (design 2026-10-17 v120-completeness R4c).

After a failed local-box attempt (connect failure, timeout, empty answer, invalid JSON or any other
local infrastructure_error) the box is skipped for ``local_backoff_minutes`` (default 15, 0 off) and
the Haiku-class fast model answers instead. The next call after expiry tries the box again: a success
clears the marker and a failure renews it.

One marker, shared with worker/hooks/plan-validator.sh: ``local-box-backoff.json`` holding
``{"endpoint", "until"}`` (epoch seconds) in ``$IRONCLAUDE_LOCAL_BACKOFF_DIR`` (default
``~/.ironclaude/state/local-backoff``). The directory must be a real 0700 directory owned by this
user, otherwise the marker is neither trusted nor written (the same check as the Brain gate root).
"""
from __future__ import annotations

import json
import logging
import math
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Mapping

from ironclaude import paths

logger = logging.getLogger(__name__)

DEFAULT_MINUTES = 15
MARKER_NAME = "local-box-backoff.json"
_DEFAULT_OLLAMA_URL = "http://localhost:11434"


def backoff_dir() -> Path:
    override = os.environ.get("IRONCLAUDE_LOCAL_BACKOFF_DIR")
    if override:
        return Path(override.rstrip("/") or "/")
    return paths.home() / ".ironclaude" / "state" / "local-backoff"


def dir_problem(directory: Path) -> str | None:
    """Why the marker directory is unsafe, or None (mirrors the hooks' gate_root_problem)."""
    if directory.is_symlink():
        return "is a symlink"
    if not directory.is_dir():
        return "is not a directory"
    info = directory.stat()
    if info.st_uid != os.getuid():
        return "is not owned by the current user"
    if stat.S_IMODE(info.st_mode) & 0o077:
        return "has group or other permission bits"
    return None


def backoff_minutes(cfg: Mapping, backend: str) -> int:
    """``.<backend>.local_backoff_minutes // .local_backoff_minutes // 15`` like the hook's
    _resolve_local_backoff_minutes; a non-number or a negative value means the default."""
    value = None
    block = cfg.get(backend) if isinstance(cfg, Mapping) else None
    for source in (block, cfg):
        candidate = source.get("local_backoff_minutes") if isinstance(source, Mapping) else None
        if candidate is not None and candidate is not False:
            value = candidate
            break
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return DEFAULT_MINUTES
    return int(math.floor(value))


def endpoint_for(backend: str, block: Mapping) -> str | None:
    """The configured box URL (trailing slash dropped), the marker's key; None without a box."""
    if backend == "ollama":
        url = (block or {}).get("url") or _DEFAULT_OLLAMA_URL
    elif backend == "openai":
        url = (block or {}).get("base_url")
    else:
        return None
    return url.rstrip("/") if isinstance(url, str) and url else None


def _read_marker(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / MARKER_NAME).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_backed_off(endpoint: str | None, minutes: int, now: float | None = None) -> bool:
    if not endpoint or minutes <= 0:
        return False
    directory = backoff_dir()
    if dir_problem(directory):
        return False
    data = _read_marker(directory)
    until = data.get("until") if data else None
    if data is None or data.get("endpoint") != endpoint:
        return False
    if isinstance(until, bool) or not isinstance(until, (int, float)):
        return False
    return until > (time.time() if now is None else now)


def record_failure(endpoint: str | None, minutes: int, now: float | None = None) -> None:
    if not endpoint or minutes <= 0:
        return
    directory = backoff_dir()
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
    except OSError as exc:
        logger.warning("Local-box backoff directory %s could not be created: %s", directory, exc)
        return
    problem = dir_problem(directory)
    if problem:
        logger.warning("Local-box backoff directory %s %s; marker not written", directory, problem)
        return
    until = int((time.time() if now is None else now) + minutes * 60)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".marker.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"endpoint": endpoint, "until": until}, handle)
        os.replace(tmp, directory / MARKER_NAME)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record_success(endpoint: str | None) -> None:
    if not endpoint:
        return
    directory = backoff_dir()
    if dir_problem(directory):
        return
    data = _read_marker(directory)
    if data is not None and data.get("endpoint") == endpoint:
        try:
            (directory / MARKER_NAME).unlink()
        except FileNotFoundError:
            pass
```

In `commander/src/ironclaude/grader.py`:
- After `from ironclaude import fast_model` (line 20), add `from ironclaude import local_backoff`.
- In `grade`, replace lines 238-248:

```python
        backend, openai_model, openai_max_tokens, cfg, spot_model, spot_thinking = state
        if backend == "haiku":
            return self._fast_model_step(profiled_system_prompt, user_prompt, schema,
                                         read_timeout, on_fast_model, None)
        local = self._grade_local(client, backend, openai_model, openai_max_tokens, cfg,
                                  spot_model, spot_thinking, profiled_system_prompt,
                                  user_prompt, schema)
        if not local.get("infrastructure_error"):
            return local
        return self._fast_model_step(profiled_system_prompt, user_prompt, schema,
                                     read_timeout, on_fast_model, local["error_detail"])
```

with:

```python
        backend, openai_model, openai_max_tokens, cfg, spot_model, spot_thinking = state
        if backend == "haiku":
            return self._fast_model_step(profiled_system_prompt, user_prompt, schema,
                                         read_timeout, on_fast_model, None)
        # Local-box backoff (design 2026-10-17 v120-completeness R4c): the same marker the hooks
        # use. A backed-off box is not asked; any local failure (including an open transport
        # breaker) renews the marker; a usable answer clears it.
        endpoint = local_backoff.endpoint_for(backend, cfg)
        minutes = local_backoff.backoff_minutes(self._full_cfg, backend)
        if local_backoff.is_backed_off(endpoint, minutes):
            return self._fast_model_step(
                profiled_system_prompt, user_prompt, schema, read_timeout, on_fast_model,
                f"local box {endpoint} backed off (local_backoff_minutes={minutes})")
        local = self._grade_local(client, backend, openai_model, openai_max_tokens, cfg,
                                  spot_model, spot_thinking, profiled_system_prompt,
                                  user_prompt, schema)
        if not local.get("infrastructure_error"):
            local_backoff.record_success(endpoint)
            return local
        local_backoff.record_failure(endpoint, minutes)
        return self._fast_model_step(profiled_system_prompt, user_prompt, schema,
                                     read_timeout, on_fast_model, local["error_detail"])
```

In `worker/config-schema/llm-backend.md`, directly after line 231 (`- **The order.** ...`), add:

```markdown
- **Local-box backoff.** A failed local-box attempt (connect failure, timeout, empty answer or invalid JSON; in the Commander, any local `infrastructure_error`, including an open circuit breaker) skips that box for `local_backoff_minutes` and goes straight to the Haiku-class model, in the hooks and in the Commander's local grader. Resolution is block-then-top: `<backend>.local_backoff_minutes` // `local_backoff_minutes` // `15`; `0` disables it. The hooks and the Commander share one marker, `local-box-backoff.json`, holding the endpoint URL and its expiry, in `$IRONCLAUDE_LOCAL_BACKOFF_DIR` (default `~/.ironclaude/state/local-backoff`). That directory must be a real 0700 directory owned by the user; otherwise the marker is ignored and never written. The first call after expiry tries the box again: a success clears the marker and a failure renews it. The shadow spot and session summarization keep their own handling.
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_local_backoff.py tests/test_local_grader.py tests/test_fast_model_chain.py tests/test_grader.py tests/test_grade_schema_bounds.py tests/test_ollama_circuit_breaker.py tests/test_openai_client.py tests/test_shadow_grader.py tests/test_paths.py tests/test_real_path_isolation.py -q > /private/tmp/claude-502/taf2/t9-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t9-green.log; tail -n 1 /private/tmp/claude-502/taf2/t9-green.log; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py tests/test_daemon.py -q -k "grader or Grader or local or Local" > /private/tmp/claude-502/taf2/t9-green-orch.log 2>&1; echo "orch=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t9-green-orch.log; tail -n 1 /private/tmp/claude-502/taf2/t9-green-orch.log; rg -n -e 'local_backoff_minutes' /Users/roberthyatt/Code/ironclaude/worker/config-schema/llm-backend.md
```

Expected:
- `green=0` and `orch=0`, 0 failed.
- Exactly one `llm-backend.md` line, the new Local-box backoff bullet.

If an existing test that fails the box and then expects a second local request now fails, STOP and report it with the test name; do not weaken the backoff.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/local_backoff.py commander/src/ironclaude/grader.py commander/tests/test_local_backoff.py worker/config-schema/llm-backend.md
```

Expected: no output.

---

## Task 10: Drift rule (b) counts only in-progress rows (R4d, Part G) (depends on 9)

**Files:**
- Modify `worker/hooks/execution-drift-check.sh`:
  - the header comment, line 37 (rule (b)) and line 40 (the executing/reviewing residual);
  - the `_drift_foreign_paths` comment, lines 410-411;
  - the rule (b) liveness query, line 443.
- Modify `worker/hooks/tests/test-execution-drift-check.sh`:
  - `EXPECTED_PASSES=52` at line 26;
  - the `# 42-52.` comment, lines 506-509;
  - a new case 53 after case 52 (after line 655, `rm -f "$REPO/x52.txt"`).

**Step 1: Test (RED)**

In `worker/hooks/tests/test-execution-drift-check.sh`:
- Line 26 becomes `EXPECTED_PASSES=53`.
- Replace lines 506-509:

```bash
# 42-52. Cross-session exclusion (2026-10-17 hotfix R2-R4). Another session's recent
#        file-tool write (writer log) or a live session's same-root ledger path, charged
#        to this session's window, never blocks; this session's own shell side quest,
#        a finished session's ledger, an old record and a record elsewhere still block.
```

with:

```bash
# 42-53. Cross-session exclusion (2026-10-17 hotfix R2-R4; v120-completeness R4d). Another
#        session's recent file-tool write (writer log) or a live session's same-root
#        in-progress ledger path, charged to this session's window, never blocks; this
#        session's own shell side quest, a finished session's ledger, a completed row's
#        ledger, an old record and a record elsewhere still block.
```

- After case 52's `rm -f "$REPO/x52.txt"` and before `printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"`, insert:

```bash

# 53. Negative: a completed row's same-root ledger never excludes this session's own
#     write, even while its session stays executing (v120-completeness R4d). Case 46 is
#     the positive control: the same session's in-progress row still excludes.
sqlite3 "$DB" "INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${OTHER}', 2, 1, 'Other finished task', 'completed');"
OTHER_DONE_ROW=$(sqlite3 "$DB" "SELECT MAX(id) FROM wave_tasks WHERE terminal_session='${OTHER}';")
OTHER_DONE_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-${OTHER}.${OTHER_DONE_ROW}.txt"
printf 's53.txt\n' > "$OTHER_DONE_LEDGER"
printf '%s\n' "$ROOT_PHYS" > "${OTHER_DONE_LEDGER%.txt}.root"
STAGE53=$(sqlite3 "$DB" "SELECT workflow_stage FROM sessions WHERE terminal_session='${OTHER}';")
shell_event PreToolUse w53 "touch s53.txt" >/dev/null
printf 's\n' > "$REPO/s53.txt"
shell_event PostToolUse w53 "touch s53.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$STAGE53" = "executing" ] && [ "$RC" = "2" ] && grep -qxF "  - s53.txt" "$TMP_ROOT/err" && ok "53 a completed row's same-root ledger never excludes this session's own write" || bad "53 a completed row's same-root ledger never excludes this session's own write (stage=$STAGE53 rc=$RC: $(cat "$TMP_ROOT/err"))"
rm -f "$REPO/s53.txt"
```

Case 53 reuses `OTHER`, which case 46 left `executing` with an in-progress row whose ledger lists only `shared46.txt`. Cases 46-52 each remove their file, and case 46 asserts that submit passes, so at case 53 the only dirty ledger path of this session is `s53.txt`. No writer record names `s53.txt`, so rule (a) cannot exclude it.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/taf2/tdrift-red.log 2>&1; echo "rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/tdrift-red.log; tail -n 1 /private/tmp/claude-502/taf2/tdrift-red.log
```

Expected: `rc=1`. Exactly one FAIL line, beginning `FAIL  53 a completed row's same-root ledger never excludes this session's own write (stage=executing rc=0:`. The last line is `Results: 52 pass, 1 fail`. Today rule (b) checks only the other session's stage, so the completed row's ledger excludes `s53.txt` and submit passes.

**Step 2: Implement (GREEN)**

In `worker/hooks/execution-drift-check.sh`:
- Replace line 37, `#   row belongs to a session in executing or reviewing (professional_mode not checked).`, with:

```bash
#   row is in_progress and belongs to a session in executing or reviewing
#   (professional_mode not checked; v120-completeness R4d).
```

- Replace line 40, `#   executing/reviewing keeps its ledgers live until its next create_plan or reset; a`, with:

```bash
#   executing/reviewing keeps its in-progress rows' ledgers live until its next
#   create_plan or reset; a
```

- Replace lines 410-411:

```bash
#       ROOT, and wave_tasks row <row> belongs to <other> while <other> is executing or
#       reviewing (professional_mode is not checked).
```

with:

```bash
#       ROOT, and wave_tasks row <row> belongs to <other>, is in_progress, and <other> is
#       executing or reviewing (professional_mode is not checked).
```

- Replace line 443:

```bash
    live=$(_drift_sql "SELECT 1 FROM wave_tasks w JOIN sessions s ON s.terminal_session=w.terminal_session WHERE w.id=$row AND w.terminal_session='$(_drift_q "$other")' AND s.workflow_stage IN ('executing','reviewing') LIMIT 1;")
```

with:

```bash
    live=$(_drift_sql "SELECT 1 FROM wave_tasks w JOIN sessions s ON s.terminal_session=w.terminal_session WHERE w.id=$row AND w.terminal_session='$(_drift_q "$other")' AND w.status='in_progress' AND s.workflow_stage IN ('executing','reviewing') LIMIT 1;")
```

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/taf2/tdrift-green.log 2>&1; echo "check=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/tdrift-green.log; tail -n 1 /private/tmp/claude-502/taf2/tdrift-green.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-identity.sh > /private/tmp/claude-502/taf2/tdrift-identity.log 2>&1; echo "identity=$?"; tail -n 1 /private/tmp/claude-502/taf2/tdrift-identity.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-roots.sh > /private/tmp/claude-502/taf2/tdrift-roots.log 2>&1; echo "roots=$?"; tail -n 1 /private/tmp/claude-502/taf2/tdrift-roots.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-writers.sh > /private/tmp/claude-502/taf2/tdrift-writers.log 2>&1; echo "writers=$?"; tail -n 1 /private/tmp/claude-502/taf2/tdrift-writers.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-pmguard-codex-primary.sh > /private/tmp/claude-502/taf2/tdrift-pmguard.log 2>&1; echo "pmguard=$?"; tail -n 1 /private/tmp/claude-502/taf2/tdrift-pmguard.log; rg -n -F "AND w.status='in_progress' AND s.workflow_stage IN ('executing','reviewing')" /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh
```

Expected:
- `check=0`, no FAIL lines, and `Results: 53 pass, 0 fail`. Case 46 still passes, so an in-progress row of a live session still excludes.
- `identity=0` with `Results: 13 pass, 0 fail` on this Mac's case-insensitive volume (`Results: 12 pass, 0 fail` on a case-sensitive one).
- `roots=0` with `Results: 8 pass, 0 fail`.
- `writers=0` with `Results: 10 pass, 0 fail`.
- `pmguard=0` with `Results: 7 pass, 0 fail`.
- The last `rg` prints exactly one line, the rule (b) query.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-check.sh
```

Expected: no output.

---

## Task 14: Local-box backoff counts only box failures (R4c amendment) (depends on 10)

Task 14 runs before Task 11. The full Commander suite showed `test_daemon.py::TestGradeBoundedFastLaneReadTimeout::test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout` failing: its first call, bounded by a caller `read_timeout` of 0.2 s, wrote the marker, so the second call skipped the box (`assert mock_post.call_count == 2` at line 5664 got 1). The design amendment says a box failure is a connect failure, an HTTP error, an empty answer, invalid JSON, or a read timeout at the box's own configured read timeout. Its "The hook's budget counts" bullet keeps the hook's own budget timeout (`curl --max-time` = `hook_validation_budget_seconds`) a box failure, so this task changes no hook file. The only exemption is a Commander per-call `read_timeout` shorter than the Commander client's configured read timeout: a read timeout under it writes no marker and clears none.

**Grounding (current source):**
- **Commander, the two timeouts.** `LocalGrader._get_client` (grader.py:142-145) gives the normal client the read timeout `_timeout_override`, else `resolve_backend(cfg, "grader").timeout`, else 120. `resolve_backend` (backend_resolver.py:81) reads `timeout_seconds` from the backend block, then top level, for both ollama and openai. A per-call `read_timeout` builds a separate client bounded to exactly that value (grader.py:152-176, `_build_client(cfg, resolved, read_timeout)`). The fast lane passes `read_timeout = fast_lane_grade_timeout_seconds` (default 5, main.py:3257).
- **Commander, telling a read timeout apart.** Both clients map `requests.ConnectTimeout` to `OllamaConnectionError` and any other `requests.Timeout` to `OllamaTimeoutError` (ollama_client.py:369-376; openai_client.py:157-164). `_attempt` re-raises the last failure's type (ollama_client.py:250; openai_client.py:129). HTTP errors are `OllamaHTTPError` (ollama_client.py:20). `_grade_local` (grader.py:302-307) collapses every `OllamaError` into `{"infrastructure_error": True, "error_detail": str}`, so `grade()` (grader.py:255-258) cannot tell them apart and records a failure for all of them.
- **Hook (unchanged).** `call_validation_llm` calls the box with `curl --connect-timeout "$connect_to" --max-time "$hook_budget"`, where `hook_budget` is `hook_validation_budget_seconds`, default 8. Under the amendment's "The hook's budget counts" bullet, an empty result after that `--max-time` is a box failure, which is what Task 8's record step already does. `worker/hooks/plan-validator.sh` and `worker/hooks/tests/test-local-backoff.sh` are not edited; their suites are a regression check only.
- **Scope decision.** The constructor `timeout` (`_timeout_override`, the message grader's 120 s, orchestrator_mcp.py:479, 533) is the grader's configured read timeout, as `_get_client` uses it. Only a per-call `read_timeout` is a caller deadline.

**Files:**
- Modify `commander/src/ironclaude/grader.py`: the import at line 22, the backoff comment and record step in `LocalGrader.grade` (lines 243-260), a new helper after `grade`, and the `OllamaError` handler in `_grade_local` (lines 304-307).
- Modify `commander/src/ironclaude/local_backoff.py`: the module docstring, lines 3-6.
- Modify `commander/tests/test_local_backoff.py`: append three tests.
- Modify `worker/config-schema/llm-backend.md`: the `Local-box backoff` bullet, line 232.

**Step 1: Tests (RED)**

Append to `commander/tests/test_local_backoff.py`:

```python


def _ollama_config(tmp_path, block=None):
    cfg = {"backend": "ollama"}
    if block is not None:
        cfg["ollama"] = block
    path = tmp_path / "hooks-config.json"
    path.write_text(json.dumps(cfg))
    return str(path)


def test_caller_deadline_read_timeout_writes_and_clears_no_marker(monkeypatch, tmp_path):
    """R4c amendment: a read timeout under a caller read_timeout (0.2 s) shorter than the box's own
    read timeout (no timeout_seconds -> 120 s) is the caller's deadline, not a box failure."""
    from unittest.mock import patch
    import requests
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import OllamaClient
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path=_ollama_config(tmp_path))
    marker = local_backoff.backoff_dir() / local_backoff.MARKER_NAME
    with patch("ironclaude.ollama_client.requests.post", side_effect=requests.ReadTimeout()) as post, \
            patch.object(OllamaClient, "_probe_reachable", return_value=True):
        assert grader.grade("s", "u", SCHEMA, read_timeout=0.2) == {"ok": True, "src": "fast"}
        assert post.call_count == 1
        assert not marker.exists()
        local_backoff.record_failure(LOCALHOST, 15, now=time.time() - 3600)  # an expired marker
        expired = marker.read_text()
        assert grader.grade("s", "u", SCHEMA, read_timeout=0.2) == {"ok": True, "src": "fast"}
        assert post.call_count == 2  # expired: the box was asked
    assert marker.read_text() == expired  # neither cleared nor renewed


def test_read_timeout_at_the_box_configured_timeout_writes_the_marker(monkeypatch, tmp_path):
    """A read timeout at the box's own configured read timeout (ollama.timeout_seconds) is a box failure."""
    from unittest.mock import patch
    import requests
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import OllamaClient
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path=_ollama_config(tmp_path, {"timeout_seconds": 30}))
    with patch("ironclaude.ollama_client.requests.post", side_effect=requests.ReadTimeout()) as post, \
            patch.object(OllamaClient, "_probe_reachable", return_value=True):
        assert grader.grade("s", "u", SCHEMA) == {"ok": True, "src": "fast"}
        assert post.call_args.kwargs["timeout"][1] == 30  # the box's own read timeout was in force
        assert local_backoff.is_backed_off(LOCALHOST, 15)
        local_backoff.record_success(LOCALHOST)
        # A per-call read_timeout equal to the box's own is not shorter: still a box failure.
        assert grader.grade("s", "u", SCHEMA, read_timeout=30) == {"ok": True, "src": "fast"}
        assert post.call_args.kwargs["timeout"][1] == 30
    assert local_backoff.is_backed_off(LOCALHOST, 15)


def test_connect_failure_under_a_caller_deadline_writes_the_marker(monkeypatch, tmp_path):
    """A connect timeout is a box failure even under a short caller read_timeout."""
    from unittest.mock import patch
    import requests
    from ironclaude import local_backoff
    from ironclaude.grader import LocalGrader
    _fast_answer(monkeypatch, {"ok": True, "src": "fast"})
    grader = LocalGrader(config_path=_ollama_config(tmp_path))
    with patch("ironclaude.ollama_client.requests.post", side_effect=requests.ConnectTimeout()) as post:
        assert grader.grade("s", "u", SCHEMA, read_timeout=0.2) == {"ok": True, "src": "fast"}
        assert post.call_count == 1
    assert local_backoff.is_backed_off(LOCALHOST, 15)
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_local_backoff.py "tests/test_daemon.py::TestGradeBoundedFastLaneReadTimeout::test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout" -q > /private/tmp/claude-502/taf2/t14-red-py.log 2>&1; echo "red_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t14-red-py.log; tail -n 1 /private/tmp/claude-502/taf2/t14-red-py.log
```

Expected:
- `red_py=1`. FAILED exactly 2: `tests/test_local_backoff.py::test_caller_deadline_read_timeout_writes_and_clears_no_marker` (at `assert not marker.exists()`) and `tests/test_daemon.py::TestGradeBoundedFastLaneReadTimeout::test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout` (at `assert mock_post.call_count == 2`, unchanged). `test_read_timeout_at_the_box_configured_timeout_writes_the_marker` and `test_connect_failure_under_a_caller_deadline_writes_the_marker` pass: today every failure writes the marker. They guard the GREEN change against exempting too much; the configured-timeout test also pins the boundary, since a per-call `read_timeout` equal to the box's own still counts.
- The tail line begins with `2 failed, 12 passed` (derived: the file's 10 existing tests plus 3 new, plus the one daemon test).

**Step 2: Implement (GREEN)**

In `commander/src/ironclaude/grader.py`:
- Replace line 22, `from ironclaude.ollama_client import OllamaClient, OllamaError`, with:

```python
from ironclaude.ollama_client import OllamaClient, OllamaError, OllamaTimeoutError
```

- Replace lines 243-245:

```python
        # Local-box backoff (design 2026-10-17 v120-completeness R4c): the same marker the hooks
        # use. A backed-off box is not asked; any local failure (including an open transport
        # breaker) renews the marker; a usable answer clears it.
```

with:

```python
        # Local-box backoff (design 2026-10-17 v120-completeness R4c): the same marker the hooks
        # use. A backed-off box is not asked; a box failure renews the marker; a usable answer
        # clears it. A read timeout under a per-call read_timeout shorter than the box's own read
        # timeout is the caller's deadline, not a box failure: it writes no marker and clears none
        # (R4c amendment).
```

- Replace lines 258-260:

```python
        local_backoff.record_failure(endpoint, minutes)
        return self._fast_model_step(profiled_system_prompt, user_prompt, schema,
                                     read_timeout, on_fast_model, local["error_detail"])
```

with:

```python
        if not (local.get("read_timeout") and self._caller_deadline_shorter(read_timeout)):
            local_backoff.record_failure(endpoint, minutes)
        return self._fast_model_step(profiled_system_prompt, user_prompt, schema,
                                     read_timeout, on_fast_model, local["error_detail"])

    def _caller_deadline_shorter(self, read_timeout: float | None) -> bool:
        """True when a per-call read_timeout is shorter than the box's own read timeout, the one
        the normal client uses (_get_client: the constructor timeout, else timeout_seconds, else 120)."""
        if read_timeout is None:
            return False
        box_timeout = self._timeout_override
        if box_timeout is None:
            box_timeout = resolve_backend(self._full_cfg, "grader").timeout
        return read_timeout < (box_timeout if box_timeout is not None else 120)
```

- Replace lines 304-307:

```python
        except OllamaError as e:
            detail = str(e)
            logger.warning(detail)
            return self._build_infrastructure_error(detail)
```

with:

```python
        except OllamaError as e:
            detail = str(e)
            logger.warning(detail)
            error = self._build_infrastructure_error(detail)
            if isinstance(e, OllamaTimeoutError):
                error["read_timeout"] = True  # connected, then the read timed out
            return error
```

In `commander/src/ironclaude/local_backoff.py`, replace lines 3-6:

```python
After a failed local-box attempt (connect failure, timeout, empty answer, invalid JSON or any other
local infrastructure_error) the box is skipped for ``local_backoff_minutes`` (default 15, 0 off) and
the Haiku-class fast model answers instead. The next call after expiry tries the box again: a success
clears the marker and a failure renews it.
```

with:

```python
After a box failure (connect failure, HTTP error, empty answer, invalid JSON, a read timeout at the
box's own configured read timeout, or any other local infrastructure_error) the box is skipped for
``local_backoff_minutes`` (default 15, 0 off) and the Haiku-class fast model answers instead. The
hooks count their own budget timeout (hook_validation_budget_seconds) as a box failure. The only
exemption is a Commander read timeout under a per-call read_timeout shorter than the box's own read
timeout: it writes no marker and clears none (R4c amendment; LocalGrader.grade decides). The next
call after expiry tries the box again: a success clears the marker and a failure renews it.
```

In `worker/config-schema/llm-backend.md`, replace line 232 (the `- **Local-box backoff.**` bullet) with:

```markdown
- **Local-box backoff.** A box failure skips that box for `local_backoff_minutes` and goes straight to the Haiku-class model, in the hooks and in the Commander's local grader. A box failure is a connect failure (refused or connect timeout), an HTTP error, an empty answer, invalid JSON, a read timeout at the box's own read timeout (`timeout_seconds`; unset, 120 s in the Commander), or, in the Commander, any other local `infrastructure_error`. The hooks also count their own budget timeout (`--max-time` = `hook_validation_budget_seconds`, default 8) as a box failure. The only thing that does not count is a Commander read timeout under a per-call `read_timeout` shorter than the Commander's configured read timeout, such as the fast lane's `fast_lane_grade_timeout_seconds` (default 5): it writes no marker and clears none. Resolution is block-then-top: `<backend>.local_backoff_minutes` // `local_backoff_minutes` // `15`; `0` disables it. The hooks and the Commander share one marker, `local-box-backoff.json`, holding the endpoint URL and its expiry, in `$IRONCLAUDE_LOCAL_BACKOFF_DIR` (default `~/.ironclaude/state/local-backoff`). That directory must be a real 0700 directory owned by the user; otherwise the marker is ignored and never written. The first call after expiry tries the box again: a success clears the marker and a failure renews it. The shadow spot and session summarization keep their own handling.
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_local_backoff.py "tests/test_daemon.py::TestGradeBoundedFastLaneReadTimeout" tests/test_local_grader.py tests/test_fast_model_chain.py tests/test_grader.py tests/test_grade_schema_bounds.py tests/test_ollama_circuit_breaker.py tests/test_openai_client.py tests/test_shadow_grader.py -q > /private/tmp/claude-502/taf2/t14-green-py.log 2>&1; echo "green_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t14-green-py.log; tail -n 1 /private/tmp/claude-502/taf2/t14-green-py.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-local-backoff.sh > /private/tmp/claude-502/taf2/t14-green-hook.log 2>&1; echo "hook=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf2/t14-green-hook.log; tail -n 1 /private/tmp/claude-502/taf2/t14-green-hook.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf2/t14-green-fast.log 2>&1; echo "fast=$?"; tail -n 1 /private/tmp/claude-502/taf2/t14-green-fast.log; rg -n -e 'fast_lane_grade_timeout_seconds' /Users/roberthyatt/Code/ironclaude/worker/config-schema/llm-backend.md; git -C /Users/roberthyatt/Code/ironclaude diff --exit-code --name-only -- worker/hooks/plan-validator.sh worker/hooks/tests/test-local-backoff.sh; echo "hook_unstaged_rc=$?"
```

Expected:
- `green_py=0`, 0 failed, no FAILED or ERROR lines. `test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout` passes unchanged.
- Regression, hook files unchanged: `hook=0`, no FAIL lines, and `Results: 25 passed, 0 failed` (Task 8's count); `fast=0` with `Results: 76 passed, 0 failed`.
- The `rg` prints exactly one line, the `Local-box backoff` bullet.
- The `git diff --exit-code --name-only` prints nothing and `hook_unstaged_rc=0`: Task 14 left `plan-validator.sh` and `test-local-backoff.sh` as Task 8 staged them.

Any hook suite failure: STOP and report it; do not edit a hook file in this task.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/grader.py commander/src/ironclaude/local_backoff.py commander/tests/test_local_backoff.py worker/config-schema/llm-backend.md
```

Expected: no output.

---

## Task 15: One skip rule: the grader bypasses the transport breaker (R4c amendment 2026-10-18) (depends on 14)

Task 15 runs after Task 14 and before Task 11. The design's "Amendment (2026-10-18): one skip rule" makes the shared marker the only skip decision in the fast-model chain. Today the grader's box call goes `_grade_local` → `post_generate` → `_attempt`, which consults the Commander's in-process transport breaker (`ollama_client.py:223-224`, `openai_client.py:102-103`). That breaker opens after 3 consecutive connection failures for 5 s, doubling on each failed probe up to 300 s (`ollama_client.py:39-41, 89-103`), and other callers feed it. So at `local_backoff_minutes: 0` the grader could still skip the box, and another caller's failures could make it skip a box the marker calls healthy.

**Grounding (current source):**
- **Clients.** `OllamaClient.__init__` (ollama_client.py:187-202) and `OpenAiClient.__init__` (openai_client.py:39-56) take `url`/`base_url`, a fallback, `timeout`, `connect_timeout` and `probe_timeout`. Both `_attempt` loops (ollama_client.py:212-250; openai_client.py:97-129) open with `if not _BREAKERS.allow(url): continue`, then record `record_success`, `record_failure` or `record_slow` for each outcome. When no URL was tried they raise `OllamaConnectionError` whose message holds `circuit open`. The class is `OpenAiClient`, not `OpenAIClient`.
- **Factory.** `make_client` (backend_resolver.py:101-131) returns `OpenAiClient(...)` for the openai backend, else `OllamaClient(...)`.
- **Grader.** `LocalGrader._build_client` (grader.py:107-129) builds the openai client through `make_client(resolved, timeout=timeout)` (line 117) and the ollama client directly as `OllamaClient(...)` (lines 122-128). Both `_get_client` and `_get_fast_client_state` call it, so the normal and fast-lane clients share it. Task 14 does not touch lines 107-129.
- **Other callers keep the default.** `orchestrator_mcp.py:843` and `:6647` and `shadow_grader.py:160` and `:171` build clients without `use_breaker`, so they keep the breaker.
- **Tests.** The conftest autouse fixture `_reset_ollama_breakers` (conftest.py:223-230) calls `_BREAKERS.reset()` before and after every test, so the new tests need no reset of their own. `test_grader.py:220-226` (`TestConfigLoading::test_ollama_backend_without_url_uses_localhost_defaults`) asserts the exact `OllamaClient(...)` keywords the grader passes, so it gains `use_breaker=False` in GREEN. It is the only test that asserts the grader's constructor keywords (`grader.OllamaClient` is patched only in `test_grader.py`, `test_fast_model_chain.py` and `test_shadow_grader.py`; the others read `kw.get("timeout")` or assert not called). `test_fast_model_chain.py:42-49` (`test_open_breaker_falls_back_without_a_local_request`) pins the old behavior: it opens the breaker and patches `requests.post` with `side_effect=AssertionError(...)`. After the change the grader sends the request, `_attempt`'s `except BaseException` re-raises the `AssertionError` (ollama_client.py:242-244) and `_grade_local` catches only `OllamaError` (grader.py:304), so GREEN replaces that test with one that pins the new rule.
- **Reference doc.** `worker/config-schema/llm-backend.md:231` says a Commander `infrastructure_error`, "including an open circuit breaker, falls through". The grader no longer produces that error, so GREEN drops the clause.

**Files:**
- Modify `commander/src/ironclaude/ollama_client.py`: `OllamaClient.__init__` (lines 187-202) and the breaker gate in `_attempt` (lines 223-224).
- Modify `commander/src/ironclaude/openai_client.py`: `OpenAiClient.__init__` (lines 39-56) and the breaker gate in `_attempt` (lines 102-103).
- Modify `commander/src/ironclaude/backend_resolver.py`: `make_client` (lines 101-131).
- Modify `commander/src/ironclaude/grader.py`: `_build_client` (lines 116-128).
- Modify `commander/tests/test_local_backoff.py`: append three tests and a helper.
- Modify `commander/tests/test_grader.py`: the keyword assertion at lines 220-226.
- Modify `commander/tests/test_fast_model_chain.py`: replace `test_open_breaker_falls_back_without_a_local_request` (lines 42-49).
- Modify `worker/config-schema/llm-backend.md`: drop ", including an open circuit breaker," from line 231.

**Step 1: Tests (RED)**

Append to `commander/tests/test_local_backoff.py`, after Task 14's tests (they use Task 14's `_ollama_config`):

```python


OPENAI_BOX = "http://box.invalid/v1"


def _openai_config(tmp_path, block):
    path = tmp_path / "hooks-config.json"
    path.write_text(json.dumps({"backend": "openai", "openai": block}))
    return str(path)


def test_ollama_grader_asks_the_box_while_the_transport_breaker_is_open(monkeypatch, tmp_path):
    """R4c amendment (2026-10-18), one skip rule: only the shared marker makes the grader skip the
    box. An open in-process breaker (3 recorded connect failures, which any caller can feed) does not,
    so at local_backoff_minutes 0 the box is tried on every call."""
    from unittest.mock import patch
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import _BREAKERS
    _fast_answer(monkeypatch, {"ok": False, "src": "fast"})
    for _ in range(3):
        _BREAKERS.record_failure(LOCALHOST)
    assert LOCALHOST in _BREAKERS.open_urls()
    grader = LocalGrader(config_path=_ollama_config(tmp_path, {"local_backoff_minutes": 0}))
    with patch("ironclaude.ollama_client.requests.post") as post:
        post.return_value.json.return_value = {"response": '{"ok": true}'}
        assert grader.grade("s", "u", SCHEMA) == {"ok": True}
        assert post.call_count == 1
        assert post.call_args.args[0] == LOCALHOST + "/api/generate"
    assert _BREAKERS.open_urls() == []  # the outcome is still recorded: the success closed it


def test_openai_grader_asks_the_box_while_the_transport_breaker_is_open(monkeypatch, tmp_path):
    """The same rule for the OpenAI-compatible backend, whose client the grader builds via make_client."""
    from unittest.mock import patch
    from ironclaude.grader import LocalGrader
    from ironclaude.ollama_client import _BREAKERS
    _fast_answer(monkeypatch, {"ok": False, "src": "fast"})
    for _ in range(3):
        _BREAKERS.record_failure(OPENAI_BOX)
    assert OPENAI_BOX in _BREAKERS.open_urls()
    grader = LocalGrader(config_path=_openai_config(
        tmp_path, {"base_url": OPENAI_BOX, "model": "m", "local_backoff_minutes": 0}))
    with patch("ironclaude.openai_client.requests.post") as post:
        post.return_value.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        assert grader.grade("s", "u", SCHEMA) == {"ok": True}
        assert post.call_count == 1
        assert post.call_args.args[0] == OPENAI_BOX + "/chat/completions"
    assert _BREAKERS.open_urls() == []  # the outcome is still recorded: the success closed it


def test_default_clients_still_skip_an_open_breaker():
    """Positive control: a client built without use_breaker (the default every other caller uses)
    still refuses an open breaker and sends no request."""
    from unittest.mock import patch
    import pytest
    from ironclaude.ollama_client import OllamaClient, OllamaConnectionError, _BREAKERS
    from ironclaude.openai_client import OpenAiClient
    for _ in range(3):
        _BREAKERS.record_failure(LOCALHOST)
        _BREAKERS.record_failure(OPENAI_BOX)
    # Both modules call the one requests module, so this patch sees either client's request.
    with patch("ironclaude.ollama_client.requests.post") as post:
        with pytest.raises(OllamaConnectionError, match="circuit open"):
            OllamaClient(LOCALHOST).post_generate({"model": "m", "prompt": "p", "stream": False})
        with pytest.raises(OllamaConnectionError, match="circuit open"):
            OpenAiClient(OPENAI_BOX).post_generate({"model": "m", "messages": []})
        assert post.call_count == 0
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_local_backoff.py -q > /private/tmp/claude-502/taf2/t15-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t15-red.log; tail -n 1 /private/tmp/claude-502/taf2/t15-red.log
```

Expected:
- `red=1`. FAILED exactly 2: `tests/test_local_backoff.py::test_ollama_grader_asks_the_box_while_the_transport_breaker_is_open` and `tests/test_local_backoff.py::test_openai_grader_asks_the_box_while_the_transport_breaker_is_open`, each at `assert grader.grade("s", "u", SCHEMA) == {"ok": True}`: today `_attempt` refuses the open breaker without a request, and the grader returns the fast answer `{"ok": False, "src": "fast"}`. `test_default_clients_still_skip_an_open_breaker` passes, because it pins today's default behavior.
- The tail line begins with `2 failed, 14 passed` (derived: the file's 10 tests from Task 9, plus Task 14's 3, plus these 3).

**Step 2: Implement (GREEN)**

In `commander/src/ironclaude/ollama_client.py`:
- In `OllamaClient.__init__`, replace:

```python
        connect_timeout: int = 3,
        probe_timeout: int = 3,
    ) -> None:
        self._url = url.rstrip("/")
        self._fallback_url = fallback_url.rstrip("/") if fallback_url else None
```

with:

```python
        connect_timeout: int = 3,
        probe_timeout: int = 3,
        use_breaker: bool = True,
    ) -> None:
        self._url = url.rstrip("/")
        self._fallback_url = fallback_url.rstrip("/") if fallback_url else None
        # use_breaker=False skips the _BREAKERS.allow() gate but still records every outcome. The
        # local grader uses it: there the shared local-box marker is the only skip decision
        # (design 2026-10-17 v120-completeness R4c amendment 2026-10-18).
        self._use_breaker = use_breaker
```

- In `_attempt`, replace:

```python
            if not _BREAKERS.allow(url):
                continue
```

with:

```python
            if self._use_breaker and not _BREAKERS.allow(url):
                continue
```

In `commander/src/ironclaude/openai_client.py`:
- In `OpenAiClient.__init__`, replace:

```python
        connect_timeout: int = 3,
        probe_timeout: int = 3,
    ) -> None:
        if not base_url:
```

with:

```python
        connect_timeout: int = 3,
        probe_timeout: int = 3,
        use_breaker: bool = True,
    ) -> None:
        if not base_url:
```

- After `self._probe_timeout = probe_timeout` (line 56), add:

```python
        # use_breaker=False: see OllamaClient (R4c amendment 2026-10-18).
        self._use_breaker = use_breaker
```

- In `_attempt`, replace:

```python
            if not _BREAKERS.allow(url):
                continue
```

with:

```python
            if self._use_breaker and not _BREAKERS.allow(url):
                continue
```

In `commander/src/ironclaude/backend_resolver.py`, replace `make_client` (lines 101-131) with:

```python
def make_client(
    resolved: ResolvedBackend,
    timeout: int = 120,
    fallback: str | None = None,
    connect_timeout: int | None = None,
    probe_timeout: int | None = None,
    use_breaker: bool = True,
):
    """Build the transport client for a resolved backend.

    Returns an OpenAiClient for the openai backend, else an OllamaClient.
    connect_timeout/probe_timeout: explicit value wins; else falls back to the
    resolved backend's config value; else defaults to 3.
    use_breaker: passed to the client; False skips the transport breaker's allow()
    gate but still records outcomes (the local grader, R4c one skip rule).
    """
    fallback_url = fallback if fallback is not None else resolved.fallback_url
    ct = connect_timeout if connect_timeout is not None else (resolved.connect_timeout or 3)
    pt = probe_timeout if probe_timeout is not None else (resolved.probe_timeout or 3)
    if resolved.backend == "openai":
        return OpenAiClient(
            base_url=resolved.url,
            fallback_base_url=fallback_url,
            timeout=timeout,
            connect_timeout=ct,
            probe_timeout=pt,
            use_breaker=use_breaker,
        )
    return OllamaClient(
        url=resolved.url,
        fallback_url=fallback_url,
        timeout=timeout,
        connect_timeout=ct,
        probe_timeout=pt,
        use_breaker=use_breaker,
    )
```

In `commander/src/ironclaude/grader.py`, in `_build_client`, replace lines 116-128:

```python
        if resolved.backend == "openai":
            client = make_client(resolved, timeout=timeout)
            return client, "openai", resolved.model, resolved.max_tokens, cfg.get("openai", {})
        # Ollama path — construct OllamaClient directly (by name) so the
        # existing test seams that patch `grader.OllamaClient` stay intact.
        ollama_cfg = cfg.get("ollama", {})
        client = OllamaClient(
            url=ollama_cfg.get("url", "http://localhost:11434"),
            fallback_url=ollama_cfg.get("fallback_url"),
            timeout=timeout,
            connect_timeout=(resolved.connect_timeout or 3),
            probe_timeout=(resolved.probe_timeout or 3),
        )
```

with:

```python
        # One skip rule (design 2026-10-17 v120-completeness R4c amendment 2026-10-18): the shared
        # local-box marker is the only skip decision, so both clients bypass the transport breaker's
        # allow() gate. They still record every outcome for the heartbeat and the other callers.
        if resolved.backend == "openai":
            client = make_client(resolved, timeout=timeout, use_breaker=False)
            return client, "openai", resolved.model, resolved.max_tokens, cfg.get("openai", {})
        # Ollama path — construct OllamaClient directly (by name) so the
        # existing test seams that patch `grader.OllamaClient` stay intact.
        ollama_cfg = cfg.get("ollama", {})
        client = OllamaClient(
            url=ollama_cfg.get("url", "http://localhost:11434"),
            fallback_url=ollama_cfg.get("fallback_url"),
            timeout=timeout,
            connect_timeout=(resolved.connect_timeout or 3),
            probe_timeout=(resolved.probe_timeout or 3),
            use_breaker=False,
        )
```

In `commander/tests/test_grader.py`, replace lines 220-226:

```python
        MockClient.assert_called_once_with(
            url="http://localhost:11434",
            fallback_url=None,
            timeout=120,
            connect_timeout=3,
            probe_timeout=3,
        )
```

with:

```python
        MockClient.assert_called_once_with(
            url="http://localhost:11434",
            fallback_url=None,
            timeout=120,
            connect_timeout=3,
            probe_timeout=3,
            use_breaker=False,
        )
```

In `commander/tests/test_fast_model_chain.py`, replace lines 42-49 (`test_open_breaker_falls_back_without_a_local_request`) with:

```python
def test_open_breaker_no_longer_skips_the_box(monkeypatch, tmp_path):
    _fast_answer(monkeypatch, {"valid": True})
    url = "http://box.invalid:11434"
    cfg = _write(tmp_path, {"backend": "ollama", "ollama": {"url": url, "model": "m", "local_backoff_minutes": 0}})
    for _ in range(3):
        _BREAKERS.record_failure(url)
    with patch("requests.post") as post:
        post.return_value.json.return_value = {"response": '{"valid": false}'}
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": False}
        assert post.call_count == 1
```

In `worker/config-schema/llm-backend.md` line 231, replace `` any `infrastructure_error`, including an open circuit breaker, falls through. `` with `` any `infrastructure_error` falls through. ``

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_local_backoff.py tests/test_ollama_circuit_breaker.py tests/test_ollama_client.py tests/test_openai_client.py tests/test_backend_resolver.py tests/test_local_grader.py tests/test_grader.py tests/test_fast_model_chain.py tests/test_grade_schema_bounds.py tests/test_shadow_grader.py "tests/test_daemon.py::TestGradeBoundedFastLaneReadTimeout" -q > /private/tmp/claude-502/taf2/t15-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t15-green.log; tail -n 1 /private/tmp/claude-502/taf2/t15-green.log; rg -n -F 'use_breaker=False' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/grader.py; rg -n -F 'if self._use_breaker and not _BREAKERS.allow(url):' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/ollama_client.py /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/openai_client.py; rg -n -F 'use_breaker=use_breaker' /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/backend_resolver.py; rg -n -F 'circuit breaker' /Users/roberthyatt/Code/ironclaude/worker/config-schema/llm-backend.md; echo "breaker_doc_rc=$?"
```

Expected:
- `green=0`, 0 failed, no FAILED or ERROR lines. The three Task 15 tests and the replaced `test_open_breaker_no_longer_skips_the_box` pass, and so do the existing breaker suites, unchanged.
- The last `rg` prints nothing and `breaker_doc_rc=1`: Task 14 rewrote the line-232 bullet without the breaker clause, and this step removes the line-231 one.
- The first `rg` prints exactly two `grader.py` lines: the `make_client(..., use_breaker=False)` call and the `use_breaker=False,` keyword of `OllamaClient(...)`. The grader comment says `allow() gate`, never the literal `use_breaker=False`.
- The second `rg` prints exactly two lines, one in each client's `_attempt`.
- The third `rg` prints exactly two `backend_resolver.py` lines, one per constructor.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/ollama_client.py commander/src/ironclaude/openai_client.py commander/src/ironclaude/backend_resolver.py commander/src/ironclaude/grader.py commander/tests/test_local_backoff.py commander/tests/test_grader.py commander/tests/test_fast_model_chain.py worker/config-schema/llm-backend.md
```

Expected: no output.

---

## Task 11: Full suites (depends on 15)

No tests required: regression run only, no source change.

**Files:** Modify `docs/plans/2026-10-17-v120-completeness-findings.md`.

**Step 1: Full Commander suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/taf2/t8-suite.log 2>&1; echo "suite=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf2/t8-suite.log; tail -n 1 /private/tmp/claude-502/taf2/t8-suite.log
```

Expected: `suite=0`, 0 failed, no FAILED or ERROR lines, or exactly the Task 1 Step 3 baseline failing set. `test_daemon.py::TestGradeBoundedFastLaneReadTimeout::test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout` passes after Task 14. The passed count includes Task 14's three and Task 15's three new `test_local_backoff.py` tests (16 in that file). Record the passed count for the CHANGELOG.

**Step 2: Hook suites**

```bash
for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/test-brain-gated-actions.sh /Users/roberthyatt/Code/ironclaude/commander/hooks/tests/test-brain-hooks-canary.sh; do bash "$t" > "/private/tmp/claude-502/taf2/t8-hooks-$(basename "$t").log" 2>&1; echo "$(basename "$t") rc=$?"; done; rg -l '^FAIL' /private/tmp/claude-502/taf2/ --glob 't8-hooks-*.log'; echo "fail_files_rc=$?"
```

Expected: 47 lines, all `rc=0`: the 45 files under `worker/hooks/tests/`, including the new `test-local-backoff.sh` (25 assertions; Tasks 14 and 15 change no hook file), then the two Brain hook tests. Otherwise only the Task 1 Step 4 baseline files non-zero. No file names are printed and `fail_files_rc=1`, again unless the baseline files appear.

**Step 3: State-manager suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf2/t8-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf2/t8-npm.log
```

Expected: `npm=0`, or FAIL lines exactly the Task 1 Step 5 baseline set. The load rule applies only to the SIGTERM-resistant process-group test. Record the test count for the CHANGELOG.

**Step 4: Record**

Append `## Full suites (after change)` to the findings, with each summary line beside its Task 1 baseline.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-v120-completeness-findings.md
```

Expected: no output.

---

## Task 12: Release docs and staging (depends on 11)

No tests required: release notes only.

**Files:** Modify `CHANGELOG.md`, lines 51, 55, 64, 79, 80 and 81 of the `## 1.2.0` section, and `README.md` line 22. Append the release commit message paragraph to `docs/plans/2026-10-17-v120-completeness-findings.md`. Stage the design, this plan, its `.plan.json` and the findings.

**Step 1: CHANGELOG and README**

In `CHANGELOG.md` `## 1.2.0`:
- Replace the line-51 bullet, from `  - **Codex model.** Codex roles on this Mac use the newest installed` to `remote hosts keep the configured model.`, with:

```markdown
  - **Codex model.** IronClaude pins no Codex model. The Codex defaults are the family words `luna`, `terra`, `sol` and `astra`, mirroring Claude's `haiku`, `sonnet`, `opus` and `fable` aliases. A word resolves to the newest installed `gpt-<version>-<word>` where Codex runs: this Mac's `$CODEX_HOME/models_cache.json` for local roles, or the remote worker box's own cache, read over SSH. An explicit slug in config is used as-is, locally and remotely. If no model of the family is installed, Codex is unavailable for that call: routing falls to the next client, hooks and graders fail open, a resume is refused, the Codex Brain does not start (its log names the family and the cache path), and the advisor broker returns `codex-reviewer-model-unavailable`. `ProviderConfig.model_for` requires `host`, and a parity test reads the broker's runtime bundle.
```

- Replace line 55 (`- **The advisor-fallback Claude reviewer is the read-only `Explore` agent.**`) with:

```markdown
- **Claude reviewers use the locked `ironclaude:reviewer` agent.** The advisor fallback, the plan review and the end review dispatch a report-only plugin agent that has Read, Grep and Glob only (no Bash, Edit, Write, Skill or MCP tools); the orchestrator puts the diff and test output in its prompt.
- **The Brain gate root moved to `~/.ironclaude/state/brain-gate`.** The Commander creates it with mode 0700 and passes it to both Brains as `IRONCLAUDE_BRAIN_GATE_ROOT`. `brain-gated-actions.sh` and the hooks canary fail closed unless it is a real directory owned by the user with no group or other access. The rest of `/tmp/ic` is unchanged.
- **Local-box backoff.** If you run your own local box (Ollama or an OpenAI-compatible server), a box failure makes the hooks and the Commander's local grader skip that box for `local_backoff_minutes` and ask the Haiku-class model instead (Claude Haiku, or the newest Luna on Codex). A box failure is a connect failure (refused or connect timeout), an HTTP error, an empty answer, invalid JSON, or a read timeout at the box's own configured read timeout (`timeout_seconds`). The hooks also count their own budget timeout (`hook_validation_budget_seconds`, default 8 s) as a failure, so a box too slow for the hooks is skipped by the hooks and the Commander alike. The only timeout that does not trip the backoff is a Commander caller deadline shorter than the box's configured timeout, such as the fast lane's `fast_lane_grade_timeout_seconds` (default 5 s): nothing is recorded, so the Commander keeps asking a box that is slow but within its timeout. The default is 15 minutes; set `local_backoff_minutes` top-level or per backend (`ollama.local_backoff_minutes`, `openai.local_backoff_minutes`) in `~/.claude/ironclaude-hooks-config.json`, and `0` turns it off. The hooks and the Commander share one marker, `~/.ironclaude/state/local-backoff/local-box-backoff.json`, which holds the endpoint URL and the time it expires, in a 0700 directory owned by the user; delete it to try the box again at once. The next call after expiry tries the box again; a success clears the marker, and a failure renews it. The marker is the only thing that makes IronClaude skip your box, so with `0` your box is tried on every call.
```

- In line 64 (the `Execution drift check` bullet), replace the sentence `The check then drops a path another session wrote recently, or one that session lists in its own ledger for the same repository.` with `The check then drops a path another session wrote recently, or one that a live session's in-progress task lists in its own ledger for the same repository.` The rest of the line stays unchanged.
- Replace line 79 (the `Reviewer subagents` known issue) with:

```markdown
  - **Execution subagents.** `general-purpose` execution subagents still have Skill and MCP tools; a Skill call from one can move the parent session's stage. This is on the backlog.
```

- Replace line 80 (the `Drift ledgers` known issue, `A finished task's ledger still counts while its session is executing or reviewing, and ledgers are never pruned.`) with:

```markdown
  - **Drift ledgers.** Ledgers are never pruned.
```

- In line 81, replace `Suites: commander 4034, state-manager 273, hooks 44 files.` with `Suites: commander <N>, state-manager <M>, hooks 45 files.`, where `<N>` and `<M>` are the passed counts recorded in Task 11 Steps 1 and 3, and 45 is the number of files under `worker/hooks/tests/` after Task 8 adds `test-local-backoff.sh`.

In `README.md` `## What's New in v1.2.0`, replace the line-22 sentence `Graders carry their rubric in the prompt, and Codex roles on this Mac use the newest installed model.` with `Graders carry their rubric in the prompt, and Claude reviewers use a locked read-only agent.` After line 22, add:

```markdown
- **No pinned Codex models.** Codex defaults are family words (`luna`, `terra`, `sol`, `astra`) that resolve to the newest model Codex has installed where it runs, just as `haiku` or `opus` does for Claude. A model you name explicitly is used as-is.
- **Safer Brain gate.** The Brain's gate state lives in a private `~/.ironclaude/state/brain-gate` directory, and the Brain hooks refuse to run from anything else.
- **A down local box costs one try, not every call.** If you run your own Ollama or OpenAI-compatible box, a connect failure, an HTTP error, an empty or invalid answer, or a read timeout at the box's own `timeout_seconds` makes the hooks and the Commander skip it for `local_backoff_minutes` (default 15, `0` turns it off) and ask the Haiku-class model (Haiku, or Luna on Codex) instead. The hooks also count running out their own `hook_validation_budget_seconds` (default 8 s). Only a Commander caller deadline shorter than the box's timeout, such as the fast lane's `fast_lane_grade_timeout_seconds`, does not count. The hooks and the Commander share the marker in `~/.ironclaude/state/local-backoff/`, and it is the only thing that makes IronClaude skip your box, so with `0` your box is tried on every call. The CHANGELOG has the details.
```

```bash
rg -n -e 'ironclaude:reviewer' -e 'family words' -e 'brain-gate' -e 'local_backoff_minutes' -e 'hooks 45 files' -e 'in-progress task lists' -e 'Ledgers are never pruned' -e 'fast_lane_grade_timeout_seconds' -e 'state/local-backoff' -e 'tried on every call' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md /Users/roberthyatt/Code/ironclaude/README.md; rg -n -e 'read-only `Explore` agent' -e 'A locked reviewer agent is planned' -e 'remote hosts keep the configured model' -e 'Reviewer subagents' -e 'still counts while its session' -e 'hooks 44 files' -e 'connect failure, timeout, empty answer or invalid JSON' -e 'slow but healthy' /Users/roberthyatt/Code/ironclaude/CHANGELOG.md; echo "stale_rc=$?"; rg -n -F -e 'After the local model fails once' -e 'slow but healthy' -e 'Codex roles on this Mac use the newest installed model' /Users/roberthyatt/Code/ironclaude/README.md; echo "stale_readme_rc=$?"
```

Expected:
- `CHANGELOG.md` lines:
  - one `ironclaude:reviewer`;
  - one `family words`;
  - at least one `brain-gate`;
  - at least one `local_backoff_minutes`;
  - one `hooks 45 files`;
  - one `in-progress task lists`;
  - one `Ledgers are never pruned`;
  - one line holding `fast_lane_grade_timeout_seconds`, `hook_validation_budget_seconds`, `state/local-backoff` and `tried on every call`, the `Local-box backoff` bullet.
- `README.md` lines: one `family words`, one `brain-gate`, and one line holding `local_backoff_minutes`, `fast_lane_grade_timeout_seconds`, `hook_validation_budget_seconds`, `state/local-backoff` and `tried on every call`, the `A down local box` bullet.
- `stale_rc=1` and `stale_readme_rc=1`, with no stale lines.

**Step 2: Release commit message paragraph**

Append this section to `docs/plans/2026-10-17-v120-completeness-findings.md`, for the operator's v1.2.0 squash commit message:

```markdown
## Release commit message paragraph

Local-box backoff, for anyone running their own Ollama or OpenAI-compatible box: when the box fails, the hooks and the Commander's local grader skip it for local_backoff_minutes (default 15; 0 turns it off; set it top-level or per backend in ~/.claude/ironclaude-hooks-config.json) and ask the Haiku-class model instead (Claude Haiku, or the newest Luna on Codex). A box failure is a connect failure, an HTTP error, an empty answer, invalid JSON, or a read timeout at the box's own configured timeout_seconds. The hooks also count their own budget timeout (hook_validation_budget_seconds, default 8 s) as a failure. The only thing that does not trip the backoff is a Commander caller deadline shorter than the box's configured timeout, such as the fast lane's fast_lane_grade_timeout_seconds (default 5 s). The hooks and the Commander share one marker, ~/.ironclaude/state/local-backoff/local-box-backoff.json, in a private 0700 directory. The first call after expiry tries the box again; a success clears the marker and a failure renews it. The marker is the only thing that makes IronClaude skip your box, so with 0 your box is tried on every call.
```

```bash
rg -n -e '^## Release commit message paragraph$' -e 'fast_lane_grade_timeout_seconds' /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-17-v120-completeness-findings.md
```

Expected: exactly two lines, the heading and the paragraph; the paragraph line also holds `hook_validation_budget_seconds` and `tried on every call`.

**Step 3: Stage and check the staged set**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md README.md; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-v120-completeness-design.md docs/plans/2026-10-17-v120-completeness.md docs/plans/2026-10-17-v120-completeness.plan.json docs/plans/2026-10-17-v120-completeness-findings.md; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status
```

Expected: exactly these 56 entries. Task 14 edits only paths already listed; Task 15 adds five M entries, `ollama_client.py`, `openai_client.py`, `backend_resolver.py`, `test_grader.py` and `test_fast_model_chain.py`. `worker/.codex-plugin/plugin.json` is absent, while `worker/.claude-plugin/plugin.json` from Task 2 is present.

**A (11):**
- `worker/agents/reviewer.md`
- `commander/tests/test_reviewer_agent.py`
- `commander/tests/test_no_pinned_codex_models.py`
- `commander/tests/test_brain_gate_root.py`
- `worker/hooks/tests/test-local-backoff.sh`
- `commander/src/ironclaude/local_backoff.py`
- `commander/tests/test_local_backoff.py`
- the 4 `docs/plans/2026-10-17-v120-completeness*` files

**M (45):**
- Skills and docs:
  - `worker/.claude-plugin/plugin.json`
  - `worker/skills/advisor-fallback/SKILL.md`
  - `worker/skills/executing-plans/SKILL.md`
  - `CODEX_SETUP.md`
  - `config/ironclaude.json.example`
  - `worker/config-schema/llm-backend.md`
  - `CHANGELOG.md`
  - `README.md`
- Commander source: `config.py`, `provider_config.py`, `provider_router.py`, `codex_brain_client.py`, `fast_model.py`, `orchestrator_mcp.py`, `paths.py`, `brain_client.py`, `grader.py`, `ollama_client.py`, `openai_client.py` and `backend_resolver.py`, all under `commander/src/ironclaude/`.
- Commander tests: `conftest.py`, `test_advisor_fallback_directive.py`, `test_provider_config.py`, `test_fast_model.py`, `test_provider_router.py`, `test_codex_brain_client.py`, `test_orchestrator_mcp.py`, `test_brain_client_gate_session.py`, `test_codex_brain_hooks_canary.py`, `test_grader.py` and `test_fast_model_chain.py`, all under `commander/tests/`.
- Hooks:
  - `worker/hooks/plan-validator.sh`
  - `worker/hooks/tests/test-fast-model-fallback.sh`
  - `worker/hooks/tests/test-openai-backend.sh`
  - `worker/hooks/tests/test-last-assistant-text.sh`
  - `worker/hooks/tests/test-gbtw-continuation-judge.sh`
  - `worker/hooks/execution-drift-check.sh`
  - `worker/hooks/tests/test-execution-drift-check.sh`
  - `worker/hooks/brain-gated-actions.sh`
  - `worker/hooks/test-brain-gated-actions.sh`
  - `commander/hooks/brain-hooks-canary.sh`
  - `commander/hooks/tests/test-brain-hooks-canary.sh`
- State-manager: `src/tools/advisor-review.ts`, `src/tools/advisor-review.test.ts` and `dist/index.js`, under `worker/mcp-servers/state-manager/`.

Any other entry means STOP and report it.

After this task, the operator squashes onto `origin/main` as one v1.2.0 commit, deploys, and only then lets Task 13 run. Tag after Task 13 passes, so a failed live probe never needs a re-tag. Task 13 appends to the findings file after the commit, so its addendum is folded in when the operator re-squashes before tagging.

---

## Task 13: Post-deploy live verification, operator-assisted (R0 A1, R4, R6) (depends on 12)

No tests required: live probes after deploy. Every LLM self-report is evidence, not proof. If a probe misses its expectation, record it and STOP, then debug and fix it in this loop through a new task (design R4). Never rerun until it passes. Never write `~/.codex/config.toml`.

**Files:** Modify `docs/plans/2026-10-17-v120-completeness-findings.md`.

**Step 1: Deploy precondition (operator-assisted)**

The operator has done all of the following:
1. Committed the staged set as one untagged v1.2.0 commit.
2. Run `claude plugin update ironclaude@ironclaude` and synced the Claude cache.
3. Run `make codex-plugin-release` and `make deploy-hooks`.
4. Restarted the Commander and ChatGPT.

Then:

```bash
touch /private/tmp/claude-502/taf2/t10-start; cmp /Users/roberthyatt/Code/ironclaude/worker/agents/reviewer.md /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.2.0/agents/reviewer.md; echo "agent_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/brain-gated-actions.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-gated-actions.sh; echo "gate_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/commander/hooks/brain-hooks-canary.sh /Users/roberthyatt/.claude/ironclaude-hooks/brain-hooks-canary.sh; echo "canary_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/plan-validator.sh /Users/roberthyatt/.claude/ironclaude-hooks/plan-validator.sh; echo "validator_cmp=$?"; cmp /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh /Users/roberthyatt/.claude/ironclaude-hooks/execution-drift-check.sh; echo "drift_cmp=$?"; ls -t /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude
```

Expected:
- `agent_cmp=0`, `gate_cmp=0`, `canary_cmp=0`, `validator_cmp=0` and `drift_cmp=0`.
- Record the newest Codex cache version, the first `ls -t` line.

Any non-zero `cmp` means the deploy is incomplete: STOP and report which file.

**Step 2: A1, the reviewer agent's tools (orchestrator step)**

If `ironclaude:reviewer` is not in this session's agent list, the operator starts a fresh Claude Code session and this step runs there. The orchestrator then makes three dispatches with `model="haiku"`:
1. `Agent(subagent_type="ironclaude:reviewer")` with the prompt `List the exact names of every tool you can call in this session, comma-separated. Call no tool.`
2. `Agent(subagent_type="ironclaude:reviewer")` with the prompt `Run the shell command echo A1-PROBE with any tool you have and reply with its output, or reply NO-SHELL if you have no tool that can run it.`
3. A positive control: `Agent(subagent_type="general-purpose")` with the first prompt.

Expected:
- The first reviewer reply names exactly `Read`, `Grep` and `Glob`, with no Bash, Edit, Write, Skill, Agent or `mcp__` tool.
- The second reviewer reply is `NO-SHELL`.
- The control names `Bash`.

A Bash tool for the reviewer, or a control without Bash, means record it and STOP.

**Step 3: A Codex grader on the resolved newest model (R6)**

```bash
cat > /private/tmp/claude-502/taf2/grader_probe.py <<'PY'
"""Post-deploy probe (design 2026-10-17 v120-completeness R6): the Codex grader runs on the newest
installed Sol from this Mac's models_cache.json. Side effect outside /private/tmp: _call_grader's
idempotent ensure_brain_trusted(~/.ironclaude/grader), the same write the Commander makes at start-up."""
import copy, json, sys
from unittest.mock import MagicMock
sys.path.insert(0, "/Users/roberthyatt/Code/ironclaude/commander/src")
from ironclaude.config import DEFAULTS
from ironclaude.db import init_db
from ironclaude.provider_config import local_codex_cache_path, resolve_local_codex_model
import ironclaude.orchestrator_mcp as omcp

config = copy.deepcopy(DEFAULTS)
config["providers"]["clients"]["codex"]["enabled"] = True
config["providers"]["roles"]["grader"] = {"preferred": "codex", "clients": ["codex"]}
configured = config["providers"]["clients"]["codex"]["models"]["opus"]
print("cache=" + local_codex_cache_path())
print("configured_opus=" + configured)
print("resolved_opus=" + resolve_local_codex_model(configured))
tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(),
                               db_conn=init_db("/private/tmp/claude-502/taf2/grader-probe.db"), config=config)
tools._ensure_role_capabilities("grader", "opus")
handle = tools._provider_router()[0].resolve("grader", "opus", ["local"])
print(f"route client={handle.client} model={handle.model}")
print("result=" + json.dumps(tools._call_grader("You are a code grader. Grade the code A-F.",
                                                "def add(a, b):\n    return a + b\n")))
PY
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf2/grader_probe.py > /private/tmp/claude-502/taf2/t10-grader.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf2/t10-grader.log; jq -r '[.models[]?.slug | select(type == "string" and test("^gpt-[0-9]+(\\.[0-9]+)*-sol$"))] | join(" ")' /Users/roberthyatt/.codex/models_cache.json
```

Expected:
- `probe=0` and `configured_opus=sol`.
- `resolved_opus=` is the numerically newest of the Sol slugs on the last line.
- `route client=codex model=` equals `resolved_opus`.
- `result=` holds a `grade` and no `infrastructure_error`.

Any miss means record it and STOP.

**Step 4: Commander on Codex, R4 item 1 (operator-assisted)**

1. The operator types `/ironclaude provider brain codex` in Slack; the Commander restarts.
2. When the Brain is up, the operator asks in Slack: `what unfinished work is sitting idle right now?`
3. Then run:

```bash
rg -n -e 'Codex hook-trust warm-up finished ready=' -e 'Brain SDK client started client=codex' -e 'Brain SDK client not started client=codex' -e 'Codex Brain hooks are not running' -e 'spawn failed: Codex model family' /private/tmp/ic/daemon.log; ls -ld /Users/roberthyatt/.ironclaude/state/brain-gate; find /Users/roberthyatt/.ironclaude/state/brain-gate -name hooks-live -newer /private/tmp/claude-502/taf2/t10-start; find /private/tmp/ic/codex-brain-gate -name hooks-live -newer /private/tmp/claude-502/taf2/t10-start; find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /private/tmp/claude-502/taf2/t10-start
```

Expected:
- `daemon.log` shows, after the restart, `Codex hook-trust warm-up finished ready=True trust_prompt_answered=` followed by `Brain SDK client started client=codex`. There is no `Brain SDK client not started client=codex`, no `Codex Brain hooks are not running` and no `spawn failed: Codex model family` line after the restart.
- The `ls -ld` line shows `drwx------`.
- The first `find` prints one `.../brain-gate/<session>/hooks-live`. The old-root `find` prints nothing.
- The last `find` lists the Brain's new rollout file(s).

**Step 5: The Codex Brain's rollout has no tool errors (R4 item 1)**

```bash
find /Users/roberthyatt/.codex/sessions -name 'rollout-*.jsonl' -newer /private/tmp/claude-502/taf2/t10-start -exec rg -n -i -H -e 'professional mode is unset' -e 'access unavailable' {} +; echo "scan_done=$?"
```

Expected: no match lines, then `scan_done=0`. These are the 2026-10-10 symptom strings. The operator also confirms that the Brain's Slack answer lists real workers or ledger tasks. A pass closes the 2026-10-10 backlog entry, and the operator may switch back with `/ironclaude provider brain claude`.

**Step 6: Owed per-call tiering checks, R4 item 2 (operator-assisted checklist)**

The operator triggers each item; the agent reads the evidence:
- **1b.** The Brain runs `send_to_worker` or `post_message` to a live worker.
- **2.** The Brain runs `evaluate_worker_health` on a live worker (Sonnet class).
- **3.** The Brain spawns a `claude-opus` worker.

```bash
rg -n -e 'fast model answered via' -e 'fast model unavailable' -e 'fast model skips codex' -e 'Provider route skipped' /private/tmp/ic/daemon.log; rg -l -F '/advisor opus' /private/tmp/ic-logs
```

Expected, after the start marker:
- **1b:** a `fast model answered via haiku` or `fast model answered via luna:<slug>` line.
- **2:** a `fast model answered via sonnet` or `fast model answered via terra:<slug>` line.
- **3:** the new worker's pane log listed by the second `rg`.

If the orchestrator MCP's log for item 2 does not reach `daemon.log`, record `UNVERIFIED-LIVE: no daemon.log line for evaluate_worker_health; searched daemon.log and the Brain rollout`, and grep the Brain rollout for `evaluate_worker_health` as secondary evidence.

**Step 7: Carried R4b live items (operator-assisted)**

Run the operator steps carried from Task 7:
- **Defect 2:** Codex `/commit` with nothing staged, then read the newest `human_intents` row.
- **Defect 5:** `git add` in a managed worktree under a `workspace-write` Codex sandbox, on a scratch repository only.
- **Defect 9:** read the transcript of a fresh `/activate-professional-mode`.

Record each as LIVE or FIXED with its evidence, or keep `UNVERIFIED-LIVE` if the operator does not run it.

**Step 8: Record**

Append `## Post-deploy verification` to the findings, with every Step 1-7 output and verdict. A1 is recorded here, closing the Task 1 note.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-v120-completeness-findings.md
```

Expected: no output. The operator folds this addendum into the v1.2.0 commit before tagging.
