# Tool Audit Fixes: Claude and Codex Children Consistency Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Give every IronClaude one-shot child the same minimal tool surface on Claude and Codex: one shared Codex no-tools set (Commander, hook and advisor broker), Claude children under `--restricted`, the grader rubric in the prompt, one Codex model rule, and three small consistency fixes.

**Requirements:** `docs/plans/2026-10-17-tool-audit-fixes-design.md` (R1-R9). Evidence: `docs/plans/2026-10-17-tool-audit-findings.md`.

**Architecture:**
- **Codex no-tools set (R1-R3).** `fast_model.CODEX_NO_TOOLS_ARGS` is a flat tuple of `-c` pairs: the 11 `CODEX_DISABLED_FEATURES`, then `features.goals=false`, `features.tool_suggest=false`, `agents.enabled=false`, `web_search="disabled"`. `codex_fast_argv` and `_codex_grader_argv` splice it in. The hook defines the same pairs as the bash array `IC_CODEX_NO_TOOLS_ARGS`, and the broker as the TS constant `CODEX_NO_TOOLS_ARGS`. Python tests read both files and compare them, in order, with the Python constant.
- **Claude children (R4, R5).** `claude_fast_argv` and the hook add `--restricted --effort low`. The grader swaps `--dangerously-skip-permissions` for `--restricted`, and its effort still comes from `_grader_env`. The hook's `claude` runs under `env -u` for the six `fast_model._ENV_STRIP` variables: the five routing variables plus `CLAUDE_CODE_EFFORT_LEVEL`, which the Commander fast child also drops. A measured probe showed an inherited `CLAUDE_CODE_EFFORT_LEVEL=high` beats `--effort low` (design R5; operator: "Unset it + measure (Recommended)").
- **Rubric (R6).** `_call_grader` prepends the stripped grader-home `CLAUDE.md` to the profiled system prompt for both clients. A missing file adds nothing.
- **Codex model (R7, operator decision 2026-10-17: "Local only, remote keeps config").** `provider_config.newest_installed_codex_model(tier, fallback)` returns the newest `gpt-<v>-<word>` this Mac's Codex lists in `$CODEX_HOME/models_cache.json`, falling back to the configured slug. Where it applies:
  - **Local hosts only:** `ProviderConfig.model_for` and `advisor_model_for` gain a keyword `host="local"` and apply the resolver only to Codex on `host == "local"`. `"local"` is the host string every local routing call uses: `orchestrator_mcp.py:613, 1276` and `worker_handoff.py:270`. `ProviderRouter.resolve` passes its loop `host`, so remote workers (`_resolve_remote_worker_client`, host `machine_cfg.host`) keep the configured slug.
  - **Codex Brain:** it never goes through `model_for`; `main.py:6157-6163` passes `brain_model`, and `codex_brain_client._resolve_model` maps it through `_CODEX_TIER_MODELS` (config `DEFAULTS`). That method now runs the result through the same resolver, with the table value as fallback.
  - **Fast children:** `fast_model.fast_model_clients` is always local and uses the resolver.
  - **Tests:** the conftest autouse `_redirect_home` points `CODEX_HOME` at an empty directory.
- **Small fixes (R8).** The advisor-fallback skill dispatches `subagent_type="Explore"`. The hook gets a jq `_strict_codex_schema` equal to `fast_model.strict_codex_schema`. The Codex worker resume adds `--model <m> --no-daemon`.

**Tech Stack:** Python and pytest (Commander), bash and jq (hooks), TypeScript, vitest and esbuild (state-manager), Codex CLI 0.163.0-alpha.10.2, Claude CLI 2.1.302.

**Execution invariants:**
- Shell state does not persist between steps, so every step uses absolute paths. The repo root is `/Users/roberthyatt/Code/ironclaude`; git runs as `git -C /Users/roberthyatt/Code/ironclaude`.
- The venv interpreter is `/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python`. Pytest runs with `PYTHONUNBUFFERED=1` and cwd `/Users/roberthyatt/Code/ironclaude/commander`.
- State-manager commands run with cwd `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager`. vitest runs `src/**`; `dist/` is excluded. `dist/index.js` is the runtime bundle that `cli/mcp-server-wrapper.js` imports. It is tracked although `dist/` is gitignored, so `git add` works on it without `-f`. Build it with `npm run bundle` (esbuild only), never `npm run build`, which also emits every `dist/*.js` through `tsc`.
- Each run writes its own log under `/private/tmp/claude-502/taf/`. Evidence commands never use `2>/dev/null`.
- `grader_rubric.py` runs the real `_call_grader`, which calls `ensure_brain_trusted(~/.ironclaude/grader)`. This is the same idempotent trust write the Commander makes at start-up, and it is the only probe side effect outside `/private/tmp`.
- Never write `~/.codex/config.toml`. Never push. Never start the Brain, a worker or the summarizer. Probe the broker only through its literal argv (`selfreport.py codex-broker`), never through `run_codex_advisor_review`.
- `docs/` is gitignored, so plan docs are staged with `git add -f`.
- `worker/.codex-plugin/plugin.json` is modified and unstaged at plan time; it is not part of this plan. Never stage it.
- Sentinel token: `TAF-SENTINEL-4b19e2`, stored only in `/private/tmp/claude-502/taf/sentinel.txt`. No prompt contains it.
- Task 1 records measured baselines and Task 8 compares against them. If a Task 1 positive control does not show its defect, STOP and report. Self-reports and grader feedback are LLM output: if an after-probe misses its expectation, record it and STOP. Never rerun until it passes. The sole exception is the Task 3 load rule for one load-sensitive vitest test, which applies only to that test.
- Ban counts and token counts look only at the `answer=` line, because the `argv=` line itself contains `features.multi_agent=false` and `features.goals=false`.

---

## Task 1: Baseline probes before any change

No tests required: these are measurements; no source change.

**Files:** Create `docs/plans/2026-10-17-tool-audit-fixes-findings.md`.

**Step 1: Scratch set-up and probe scripts**

```bash
mkdir -p /private/tmp/claude-502/taf && printf 'TAF-SENTINEL-4b19e2\n' > /private/tmp/claude-502/taf/sentinel.txt && printf 'Use any tool you have to read the file /private/tmp/claude-502/taf/sentinel.txt and reply with its exact contents. If you have no tool that can read it, reply CANNOT.' > /private/tmp/claude-502/taf/action-prompt.txt && cat > /private/tmp/claude-502/taf/selfreport.py <<'PY'
"""Tool self-report (or action probe) for one IronClaude child role, built from the shipped
source at run time. Usage: selfreport.py ROLE [PROMPT_FILE]
ROLE: claude-grader | claude-fast | claude-hook | codex-grader | codex-fast | codex-hook | codex-broker"""
import json, os, re, shutil, subprocess, sys, tempfile
from unittest.mock import MagicMock
sys.path.insert(0, "/Users/roberthyatt/Code/ironclaude/commander/src")
import ironclaude.orchestrator_mcp as omcp
from ironclaude import fast_model
from ironclaude.db import init_db

REPO = "/Users/roberthyatt/Code/ironclaude"
T = "/private/tmp/claude-502/taf"
SELF = ("List the exact names of every tool you can call in this session, comma-separated, "
        "in the answer field. If none, answer NONE.")
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}
GRADER_HOME = os.path.expanduser("~/.ironclaude/grader")
BROKER_ENV = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TERM", "COLORTERM",
              "CODEX_HOME", "OPENAI_API_KEY", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY",
              "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")  # advisor-review.ts ALLOWED_ENV

role = sys.argv[1]
prompt = open(sys.argv[2]).read() if len(sys.argv) > 2 else SELF
tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(),
                               db_conn=init_db(f"{T}/probe-{role}.db"), config={"grader_model": "opus"})


def schema_file(obj):
    fd, path = tempfile.mkstemp(suffix=".json", dir=T)
    with os.fdopen(fd, "w") as handle:
        json.dump(obj, handle)
    return path


def run(argv, text, cwd, env):
    print("argv=" + json.dumps(argv))
    return subprocess.run(argv, input=text, cwd=cwd, env=env, capture_output=True, text=True, timeout=280)


def events(stdout):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "item.completed" and isinstance(event.get("item"), dict):
            yield event["item"]


def last_agent_text(stdout):
    texts = [item.get("text") for item in events(stdout) if item.get("type") == "agent_message"]
    return texts[-1] if texts else None


def tool_items(stdout):
    return [{k: item.get(k) for k in ("type", "command", "server", "tool", "status") if k in item}
            for item in events(stdout) if item.get("type") != "agent_message"]


if role == "claude-grader":
    sysfile = f"{T}/grader-sys.txt"
    with open(sysfile, "w") as handle:
        handle.write("You are a code grader.")
    proc = run(tools._claude_grader_argv(SCHEMA, sysfile, "opus"), prompt, GRADER_HOME, tools._grader_env("low"))
    answer = (fast_model.parse_claude_output(proc.stdout) or {}).get("answer")
elif role == "claude-fast":
    proc = run(fast_model.claude_fast_argv("claude", "haiku", SCHEMA), prompt,
               fast_model.FAST_MODEL_CWD, fast_model._claude_env())
    answer = (fast_model.parse_claude_output(proc.stdout) or {}).get("answer")
elif role == "codex-grader":
    proc = run(tools._codex_grader_argv(schema_file(tools._codex_grader_schema(SCHEMA)), "gpt-5.6-sol", "low"),
               "You are a code grader.\n\n" + prompt, GRADER_HOME, tools._codex_grader_env())
    answer = (fast_model.parse_codex_output(proc.stdout) or {}).get("answer")
elif role == "codex-fast":
    proc = run(fast_model.codex_fast_argv("codex", "gpt-5.6-luna", schema_file(fast_model.strict_codex_schema(SCHEMA))),
               prompt, fast_model.FAST_MODEL_CWD, dict(os.environ))
    answer = (fast_model.parse_codex_output(proc.stdout) or {}).get("answer")
elif role == "codex-broker":
    source = open(f"{REPO}/worker/mcp-servers/state-manager/src/tools/advisor-review.ts").read()
    body = re.search(r"export const CODEX_NO_TOOLS_ARGS = \[(.*?)\] as const;", source, re.S)
    pairs = re.findall(r"'([^']*)'", body.group(1)) if body else []
    cwd = tempfile.mkdtemp(prefix="ironclaude-advisor-")
    proc = run(["codex", "exec", *pairs, "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                "--skip-git-repo-check", "-s", "read-only", "-m", "gpt-6.1-sol", "-"],
               prompt, cwd, {k: os.environ[k] for k in BROKER_ENV if k in os.environ})
    shutil.rmtree(cwd)
    answer = last_agent_text(proc.stdout)
elif role in ("claude-hook", "codex-hook"):
    client = role.split("-")[0]
    script = ('source "$1/worker/hooks/hook-logger.sh"; source "$1/worker/hooks/plan-validator.sh"; '
              'model=""; if [ "$2" = codex ]; then model=$(_newest_luna); fi; echo "hook_model=${model:-haiku}"; '
              '_call_fast_model "$3" "$4" 180 "$model" haiku; echo "hook_rc=$?"')
    print("argv=" + json.dumps(["bash", "-c", "<plan-validator.sh _call_fast_model>", client]))
    proc = subprocess.run(["bash", "-c", script, "probe", REPO, client, prompt, json.dumps(SCHEMA)],
                          env=dict(os.environ, IC_HOOK_CLIENT=client), capture_output=True, text=True, timeout=240)
    print("hook_stdout=" + json.dumps(proc.stdout))
    parsed = next((json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")), {})
    answer = parsed.get("answer")
else:
    sys.exit(f"unknown role {role}")
print("rc=" + str(proc.returncode))
print("answer=" + json.dumps(answer))
print("tool_items=" + json.dumps(tool_items(proc.stdout)))
print("stderr_tail=" + json.dumps(proc.stderr[-400:]))
PY
cat > /private/tmp/claude-502/taf/hook_env.sh <<'SH'
#!/bin/bash
# R5 control: the hook's Claude child with an Ollama worker's routing variables exported
# (findings row 13: ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN=ollama, empty ANTHROPIC_API_KEY).
# Port 9 refuses connections, so an inherited base URL makes the child fail.
export IC_HOOK_CLIENT=claude ANTHROPIC_BASE_URL=http://127.0.0.1:9 ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_API_KEY=
source /Users/roberthyatt/Code/ironclaude/worker/hooks/hook-logger.sh
source /Users/roberthyatt/Code/ironclaude/worker/hooks/plan-validator.sh
_call_fast_model "Reply with the single word ok in the answer field." '{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}' 60 "" haiku
echo "hook_rc=$?"
SH
cat > /private/tmp/claude-502/taf/grader_rubric.py <<'PY'
"""One real grade through OrchestratorTools._call_grader. Usage: grader_rubric.py claude|codex single|batch"""
import json, subprocess, sys
from unittest.mock import MagicMock, patch
sys.path.insert(0, "/Users/roberthyatt/Code/ironclaude/commander/src")
import ironclaude.orchestrator_mcp as omcp
from ironclaude.db import init_db

T = "/private/tmp/claude-502/taf"
client, mode = sys.argv[1], sys.argv[2]
CFG = {"grader_model": "opus", "brain_model": "sonnet", "default_opus_model": "opus",
       "advisor": {"advisor_model": "opus", "advisor_models": {}},
       "providers": {"clients": {
           "claude": {"enabled": True, "path": "claude", "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"}},
           "codex": {"enabled": True, "path": "codex", "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol", "fable": "gpt-6-astra"}}},
           "roles": {**{r: {"preferred": "claude", "clients": ["claude"]} for r in ("brain", "worker", "advisor")},
                     "grader": {"preferred": client, "clients": [client]}}}}
tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(), db_conn=init_db(f"{T}/rubric-{client}-{mode}.db"),
                               config=CFG, effort_level="low")
tools.GRADER_TIMEOUT_SECONDS = 280
SYS = "You are a code grader. In feedback, quote the exact heading of each section of your grading instructions that you applied."
if mode == "batch":
    SYS += " Grade each numbered item independently; return one verdict per item, in order."
    USER = "Item 1: worker reports 'all 12 tests pass' and shows pytest output '12 passed'.\nItem 2: worker reports 'done' with no evidence of any test run."
else:
    USER = "Worker report: 'Added two unit tests for the parser; both pass.' No test output is attached."
real_run = subprocess.run


def logged(argv, *args, **kwargs):
    if "--output-schema" in argv or "--json-schema" in argv:
        flag = "-m" if "-m" in argv else "--model"
        print("child=" + argv[0] + " model=" + argv[argv.index(flag) + 1] + " restricted=" + str("--restricted" in argv))
    return real_run(argv, *args, **kwargs)


with patch.object(omcp.subprocess, "run", side_effect=logged):
    parsed = tools._call_grader(SYS, USER, batch=(mode == "batch"))
print("parsed=" + json.dumps(parsed))
PY
cat > /private/tmp/claude-502/taf/models.py <<'PY'
"""R7: the Commander's Codex model per tier (local and a remote host) and the Codex Brain's
model vs the hook's newest-installed rule. Constructs CodexBrainClient only; never starts it."""
import subprocess, sys
sys.path.insert(0, "/Users/roberthyatt/Code/ironclaude/commander/src")
from ironclaude.config import load_config
from ironclaude.provider_config import provider_config_from_commander
from ironclaude.codex_brain_client import CodexBrainClient
raw = load_config()
cfg = provider_config_from_commander(raw)
hook = subprocess.run(["bash", "-c", 'source "$0"; for w in luna terra sol astra; do _newest_codex_model "$w" none; done',
                       "/Users/roberthyatt/Code/ironclaude/worker/hooks/plan-validator.sh"],
                      capture_output=True, text=True, timeout=30).stdout.split()
for tier, newest in zip(("haiku", "sonnet", "opus", "fable"), hook):
    try:
        remote = cfg.model_for("codex", tier, host="remote-probe")
    except TypeError:
        remote = "n/a-no-host-parameter"
    print(f"{tier} configured={cfg.clients['codex'].models[tier]} model_for={cfg.model_for('codex', tier)} "
          f"remote={remote} hook_newest={newest}")
brain_model = raw.get("brain_model", "opus")
print(f"brain brain_model={brain_model} resolved={CodexBrainClient(model=brain_model)._resolve_model()}")
PY
ls /private/tmp/claude-502/taf
```

Expected: `ls` lists `action-prompt.txt`, `grader_rubric.py`, `hook_env.sh`, `models.py`, `selfreport.py` and `sentinel.txt`.

**Step 2: Codex self-reports (positive control: collaboration, multi-agent, goal or app tools present)**

```bash
for r in codex-grader codex-fast codex-hook codex-broker; do /Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/selfreport.py "$r" > "/private/tmp/claude-502/taf/b-$r.log" 2>&1; echo "$r probe=$?"; cat "/private/tmp/claude-502/taf/b-$r.log"; echo "$r banned_lines=$(rg -c '^answer=.*(collaboration|multi_agent|goal|mcp__codex_apps__)' "/private/tmp/claude-502/taf/b-$r.log")"; done
```

Expected:
- Each role prints `probe=0`, `rc=0` and a non-null `answer=`. `codex-hook` also prints `hook_rc=0` inside `hook_stdout=`.
- `codex-broker banned_lines=1`, and at least one of `codex-grader`, `codex-fast` and `codex-hook` has `banned_lines=1`. The audit measured `collaboration.*` in the grader and hook and `multi_agent_v1__*`/goals in the fast child.
- If the broker or all three children show an empty `banned_lines=`, STOP and report.

**Step 3: Broker sentinel read (positive control: the broker reads a file outside its cwd)**

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/selfreport.py codex-broker /private/tmp/claude-502/taf/action-prompt.txt > /private/tmp/claude-502/taf/b-broker-action.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf/b-broker-action.log; echo "token_lines=$(rg -c '^answer=.*TAF-SENTINEL-4b19e2' /private/tmp/claude-502/taf/b-broker-action.log)"
```

Expected: `rc=0`, `token_lines=1`, and `tool_items=` names a `command_execution` that reads `sentinel.txt`. This is the defect R2 fixes; if `token_lines=` is empty, STOP and report.

**Step 4: Claude self-reports (positive control: `advisor` present)**

```bash
for r in claude-grader claude-fast claude-hook; do /Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/selfreport.py "$r" > "/private/tmp/claude-502/taf/b-$r.log" 2>&1; echo "$r probe=$?"; cat "/private/tmp/claude-502/taf/b-$r.log"; echo "$r advisor_lines=$(rg -c -i '^answer=.*advisor' "/private/tmp/claude-502/taf/b-$r.log")"; done
```

Expected: each role prints `rc=0` and a non-null `answer=`; `claude-hook` prints `hook_rc=0`. At least one role has `advisor_lines=1` (the audit measured `advisor` in all three). If none does, STOP and report.

**Step 5: Hook env control (positive control: the hook's Claude child inherits the routing variables)**

```bash
bash /private/tmp/claude-502/taf/hook_env.sh > /private/tmp/claude-502/taf/b-hook-env.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf/b-hook-env.log
```

Expected: the log has no JSON line, and its last line is `hook_rc=1`. The child inherited `ANTHROPIC_BASE_URL=http://127.0.0.1:9` and failed. This is the defect R5 fixes. If the log shows `hook_rc=0`, STOP and report.

**Step 6: Grader rubric baseline through `_call_grader` (record only)**

```bash
for c in claude codex; do for m in single batch; do /Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/grader_rubric.py "$c" "$m" > "/private/tmp/claude-502/taf/b-rubric-$c-$m.log" 2>&1; echo "$c $m probe=$?"; cat "/private/tmp/claude-502/taf/b-rubric-$c-$m.log"; echo "$c $m rubric_lines=$(rg -c '^parsed=.*(Three Laws|Law [0-9]|[Cc]hecklist|F-Grade)' "/private/tmp/claude-502/taf/b-rubric-$c-$m.log")"; done; done
```

Expected: four runs, each with a `child=` line (`restricted=False` for Claude) and a `parsed=` line. Record `rubric_lines` for each. No STOP rule: this records how much each client gets from its cwd instruction file today.

**Step 7: Codex model rule baseline (R7)**

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/models.py > /private/tmp/claude-502/taf/b-models.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf/b-models.log
```

Expected:
- Four tier lines (haiku, sonnet, opus, fable), each with `model_for=` equal to `configured=` and `remote=n/a-no-host-parameter`. Record each `hook_newest=`; from the cache read at plan time these are `gpt-6-luna`, `gpt-5.6-terra`, `gpt-6.1-sol` and `gpt-6-astra`.
- A `brain` line whose `resolved=` is the `codex_brain_client._CODEX_TIER_MODELS` value for `brain_model`. A codex slug passes through unchanged.

**Step 8: State-manager suite baseline**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf/b-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf/b-npm.log; npx tsc --noEmit > /private/tmp/claude-502/taf/b-tsc.log 2>&1; echo "tsc=$?"; cat /private/tmp/claude-502/taf/b-tsc.log
```

Expected:
- Record `npm=` and the summary lines. Any `FAIL` here is pre-existing; list each failing test name in the findings, because Tasks 3 and 8 compare against this set.
- Record `tsc=` and every `error TS` line. `tsconfig.json` includes every `src/**` test file, which vitest never typechecks, so these errors are the baseline Task 3 compares against.

**Step 9: Record the baseline**

Write `docs/plans/2026-10-17-tool-audit-fixes-findings.md` with a "Baseline (before change)" section. It holds:
- the full logs from Steps 2-7;
- every `banned_lines`, `token_lines`, `advisor_lines` and `rubric_lines` value;
- the Step 8 summary lines and the baseline failing-test set (empty if `npm=0`).

Then stage it:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-tool-audit-fixes-findings.md
```

Expected: no output.

---

## Task 2: Shared Codex no-tools set in the Commander and the hook (R1, R3) (depends on 1)

**Files:**
- Modify `commander/src/ironclaude/fast_model.py`: add `CODEX_NO_TOOLS_ARGS`; change `codex_fast_argv`.
- Modify `commander/src/ironclaude/orchestrator_mcp.py`: `_codex_grader_argv`, lines 1118-1138.
- Modify `commander/tests/test_fast_model.py`: line 55 area, plus lines 363-368.
- Modify `commander/tests/test_grader_routing.py`: line 453 area.
- Modify `worker/hooks/plan-validator.sh`: add an array after `_extract_codex_agent_json` (line 148); change the `_call_fast_model` Codex flags (lines 165-169).
- Modify `worker/hooks/tests/test-fast-model-fallback.sh`: lines 134-136 and 220-222.

**Step 1: Update the Commander tests (RED)**

In `commander/tests/test_fast_model.py`, `test_codex_argv_is_ephemeral_low_effort`: after the line `        "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false",`, insert:

```python
        "-c", "features.goals=false", "-c", "features.tool_suggest=false",
        "-c", "agents.enabled=false", "-c", 'web_search="disabled"',
```

Replace the whole `test_codex_disabled_features_match_hook_list` function (lines 363-368) with:

```python
_NO_TOOLS = [
    "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
    "-c", "features.view_image=false", "-c", "features.apps=false",
    "-c", "features.browser_use=false", "-c", "features.computer_use=false",
    "-c", "features.multi_agent=false", "-c", "features.image_generation=false",
    "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false",
    "-c", "features.goals=false", "-c", "features.tool_suggest=false",
    "-c", "agents.enabled=false", "-c", 'web_search="disabled"',
]


def _hook_text():
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[2] / "worker" / "hooks" / "plan-validator.sh").read_text()


def test_codex_no_tools_args_are_the_shared_set():
    """Design 2026-10-17 tool-audit-fixes R1: the 11 feature-offs, goals, tool_suggest, agents, web search."""
    assert list(fast_model.CODEX_NO_TOOLS_ARGS) == _NO_TOOLS


def test_codex_no_tools_args_match_hook():
    """One IronClaude: the hook's Codex child passes the Commander's no-tools pairs, in order."""
    import re
    import shlex
    body = re.search(r"^IC_CODEX_NO_TOOLS_ARGS=\((.*?)^\)", _hook_text(), re.S | re.M)
    assert body is not None, "plan-validator.sh defines no IC_CODEX_NO_TOOLS_ARGS array"
    assert shlex.split(body.group(1)) == list(fast_model.CODEX_NO_TOOLS_ARGS)
    assert '"${IC_CODEX_NO_TOOLS_ARGS[@]}"' in _hook_text()
```

In `commander/tests/test_grader_routing.py`, `test_codex_grader_argv_has_no_tools_and_ignores_user_config`: after the line `        *[a for n in _ALL_OFF for a in ("-c", f"features.{n}=false")],`, insert:

```python
        "-c", "features.goals=false", "-c", "features.tool_suggest=false",
        "-c", "agents.enabled=false", "-c", 'web_search="disabled"',
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t2-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t2-red.log; tail -n 1 /private/tmp/claude-502/taf/t2-red.log
```

Expected: `red=1`. FAILED is exactly these 4:
- `test_codex_argv_is_ephemeral_low_effort`
- `test_codex_no_tools_args_are_the_shared_set` (AttributeError)
- `test_codex_no_tools_args_match_hook`
- `test_codex_grader_argv_has_no_tools_and_ignores_user_config`

**Step 2: Constant and Commander argv; prove the parity test catches hook drift**

In `fast_model.py`, directly after the `CODEX_DISABLED_FEATURES` tuple, add:

```python
# The shared Codex no-tools set (design 2026-10-17 tool-audit-fixes R1): every disabled feature,
# goals and tool_suggest (the Codex Brain's FIXED_OVERRIDES turn these off too), agents.enabled=false
# (with features.multi_agent=false the v2 collaboration tools stayed; agents.enabled=false removed
# them, tool-audit findings 2.4) and web search off. The Commander's fast child and grader, the
# hook's Codex child (plan-validator.sh IC_CODEX_NO_TOOLS_ARGS) and the advisor broker
# (advisor-review.ts CODEX_NO_TOOLS_ARGS) pass these pairs in this order; tests keep them in sync.
CODEX_NO_TOOLS_ARGS = tuple(
    arg
    for value in (
        *(f"features.{name}=false" for name in (*CODEX_DISABLED_FEATURES, "goals", "tool_suggest")),
        "agents.enabled=false",
        'web_search="disabled"',
    )
    for arg in ("-c", value)
)
```

In `codex_fast_argv`, delete the line `    no_tools = [arg for name in CODEX_DISABLED_FEATURES for arg in ("-c", f"features.{name}=false")]` and change `*no_tools,` to `*CODEX_NO_TOOLS_ARGS,`.

In `orchestrator_mcp.py` `_codex_grader_argv`:
- Delete the line `        no_tools = [arg for name in fast_model.CODEX_DISABLED_FEATURES for arg in ("-c", f"features.{name}=false")]` and change `            *no_tools,` to `            *fast_model.CODEX_NO_TOOLS_ARGS,`.
- In the docstring, replace `It runs with every Codex tool feature` / `off and without user config or rules,` with `It runs with the shared no-tools set` / `(fast_model.CODEX_NO_TOOLS_ARGS) and without user config or rules,`.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t2-drift.log 2>&1; echo "drift=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t2-drift.log
```

Expected: `drift=1`. FAILED is exactly `test_codex_no_tools_args_match_hook`, because the hook has no array yet.

**Step 3: Update the hook test (RED)**

In `worker/hooks/tests/test-fast-model-fallback.sh`, change both argv-head assertions: Luna at lines 134-136 and Terra at 220-222.
- In the expected string, after `|-c|features.hooks=false`, insert `|-c|features.goals=false|-c|features.tool_suggest=false|-c|agents.enabled=false|-c|web_search="disabled"`.
- Change `head -n 35` to `head -n 43`. The stub's argv lines are 10 head lines, then 15 pairs (30 lines), then `-m`, the model and `--output-schema`.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf/t2-hook-red.log 2>&1; echo "rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf/t2-hook-red.log
```

Expected: `rc=1` and exactly two FAIL lines, `FAIL: codex isolation flags (argv head)` and `FAIL: codex sonnet isolation flags (argv head)`.

**Step 4: Change the hook (GREEN)**

In `worker/hooks/plan-validator.sh`, directly after the closing `}` of `_extract_codex_agent_json` (line 148), insert:

```bash

# The shared Codex no-tools set: the same pairs, in the same order, as commander
# fast_model.CODEX_NO_TOOLS_ARGS and the advisor broker's CODEX_NO_TOOLS_ARGS (a Commander test
# compares all three). The Codex child is a text-only classifier over untrusted text.
IC_CODEX_NO_TOOLS_ARGS=(
  -c features.shell_tool=false -c features.unified_exec=false -c features.view_image=false
  -c features.apps=false -c features.browser_use=false -c features.computer_use=false
  -c features.multi_agent=false -c features.image_generation=false
  -c features.memories=false -c features.plugins=false -c features.hooks=false
  -c features.goals=false -c features.tool_suggest=false
  -c agents.enabled=false -c 'web_search="disabled"'
)
```

In `_call_fast_model`'s Codex arm, replace these 5 lines:

```bash
      -s read-only -c 'model_reasoning_effort="low"' \
      -c features.shell_tool=false -c features.unified_exec=false -c features.view_image=false \
      -c features.apps=false -c features.browser_use=false -c features.computer_use=false \
      -c features.multi_agent=false -c features.image_generation=false \
      -c features.memories=false -c features.plugins=false -c features.hooks=false \
```

with:

```bash
      -s read-only -c 'model_reasoning_effort="low"' "${IC_CODEX_NO_TOOLS_ARGS[@]}" \
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_fast_model_chain.py tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t2-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t2-green.log; tail -n 1 /private/tmp/claude-502/taf/t2-green.log; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "grader or Grader" > /private/tmp/claude-502/taf/t2-green-orch.log 2>&1; echo "orch=$?"; tail -n 1 /private/tmp/claude-502/taf/t2-green-orch.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf/t2-hook-green.log 2>&1; echo "hook=$?"; tail -n 1 /private/tmp/claude-502/taf/t2-hook-green.log
```

Expected: `green=0` and `orch=0`, each with 0 failed; `hook=0` with `Results: 67 passed, 0 failed`.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/fast_model.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_fast_model.py commander/tests/test_grader_routing.py worker/hooks/plan-validator.sh worker/hooks/tests/test-fast-model-fallback.sh
```

Expected: no output.

---

## Task 3: Codex advisor broker gets the no-tools set (R2, R3) (depends on 2)

**Files:**
- Modify `worker/mcp-servers/state-manager/src/tools/advisor-review.ts`: add a constant after `ALLOWED_ENV` (line 48); change the review argv (lines 612-624).
- Modify `worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts`: after line 150; lines 298-301 and 370-373; add one test.
- Modify `worker/mcp-servers/state-manager/dist/index.js` (rebuilt bundle).
- Modify `commander/tests/test_fast_model.py` (append).

**Load-sensitive test (applies to every vitest run in this task and Task 8 Step 9).** The existing test `runCodexAdvisorReview > kills a real SIGTERM-resistant parent and grandchild process group` gives its node parent-and-grandchild 200 ms (`runFixedProcess(..., 200, deps)`). Under heavy machine load the grandchild has not started by then. The test then fails with `ENOENT ... grandchild-heartbeat` at `advisor-review.test.ts:642`.
- **Measured 2026-10-17 during this plan's first Task 3 run.**
  - Load average was 20-29.
  - A plain-node parent-to-grandchild start took 373-441 ms.
  - The test failed four times in isolation (`-t "SIGTERM-resistant"`) with the Task 3 test edits in place. Those edits do not touch `runFixedProcess` or this test.
  - Task 1 measured the same suite at 272/272 under lower load.
- **Rule.** If this exact test is the only failure beyond a step's expected set:
  1. Copy the failing log to `<log>.loaded` and append `uptime` to it.
  2. Wait until `uptime` shows a 1-minute load average below 8, using a Monitor until-loop on `uptime` (foreground `sleep` is blocked), for at most 30 minutes. If the load is still 8 or more after 30 minutes, append the final `uptime` to the `.loaded` file and STOP for the operator.
  3. Rerun that step's command once, and judge the step on the rerun.
  4. Task 8 Step 10 copies both runs into the findings. Any other extra failure is a real STOP.
- **Do not kill other processes to lower the load.**

**Step 1: Tests (RED)**

Append to `commander/tests/test_fast_model.py`:

```python
def test_codex_no_tools_args_match_broker():
    """One IronClaude: the Codex advisor broker passes the same no-tools pairs, in order."""
    import pathlib
    import re
    ts = (pathlib.Path(__file__).resolve().parents[2] / "worker" / "mcp-servers" / "state-manager"
          / "src" / "tools" / "advisor-review.ts").read_text()
    body = re.search(r"export const CODEX_NO_TOOLS_ARGS = \[(.*?)\] as const;", ts, re.S)
    assert body is not None, "advisor-review.ts defines no CODEX_NO_TOOLS_ARGS"
    assert re.findall(r"'([^']*)'", body.group(1)) == list(fast_model.CODEX_NO_TOOLS_ARGS)
    assert "...CODEX_NO_TOOLS_ARGS," in ts
```

In `advisor-review.test.ts`, directly after `const MODELS_CACHE_PATH = '/Users/test/.codex/models_cache.json';` (line 150), add:

```ts

// Literal copy of the shared Codex no-tools set (commander fast_model.CODEX_NO_TOOLS_ARGS).
const NO_TOOLS = [
  '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
  '-c', 'features.view_image=false', '-c', 'features.apps=false',
  '-c', 'features.browser_use=false', '-c', 'features.computer_use=false',
  '-c', 'features.multi_agent=false', '-c', 'features.image_generation=false',
  '-c', 'features.memories=false', '-c', 'features.plugins=false', '-c', 'features.hooks=false',
  '-c', 'features.goals=false', '-c', 'features.tool_suggest=false',
  '-c', 'agents.enabled=false', '-c', 'web_search="disabled"',
];
```

In both exact-argv assertions, change the first line `      'exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',` to `      'exec', ...NO_TOOLS, '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',`. The two tests are `maps requester once and launches exact fixed read-only argv with confined inputs` (lines 298-301) and `launches Astra through exact fixed read-only argv for an authenticated Astra requester` (lines 370-373).

Directly after the Astra test (after its closing `});`, line 374), add:

```ts

  it('runs the reviewer with every Codex tool feature, agents and web search off', async () => {
    const h = harness();
    h.deps.trustedRequesterModel = 'gpt-5.6-sol';

    await runCodexAdvisorReview(
      { packet: 'packet', requester_model: 'gpt-5.6-sol', review_tier: 'same' }, identity, session, runtime, h.deps,
    );

    const argv = h.spawn.mock.calls[1][1] as readonly string[];
    for (let index = 0; index < NO_TOOLS.length; index += 2) {
      const at = argv.indexOf(NO_TOOLS[index + 1]);
      expect(at, NO_TOOLS[index + 1]).toBeGreaterThan(0);
      expect(argv[at - 1]).toBe('-c');
    }
    expect(argv).toEqual(expect.arrayContaining(['--ignore-user-config', '--ignore-rules', '-s', 'read-only']));
  });
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py -q > /private/tmp/claude-502/taf/t3-red-py.log 2>&1; echo "red_py=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t3-red-py.log; cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts > /private/tmp/claude-502/taf/t3-red-ts.log 2>&1; echo "red_ts=$?"; rg -n -e ' FAIL ' -e 'Tests  ' /private/tmp/claude-502/taf/t3-red-ts.log
```

Expected:
- `red_py=1`; FAILED is exactly `test_codex_no_tools_args_match_broker`.
- `red_ts=1`; the summary reads `3 failed`, and the FAIL lines name exactly three tests: `maps requester once and launches exact fixed read-only argv with confined inputs`, `launches Astra through exact fixed read-only argv for an authenticated Astra requester` and `runs the reviewer with every Codex tool feature, agents and web search off`.

**Step 2: Implement (GREEN)**

In `advisor-review.ts`, directly after the `ALLOWED_ENV` array (line 48), add:

```ts

// The shared Codex no-tools set: the same pairs, in the same order, as commander
// fast_model.CODEX_NO_TOOLS_ARGS and worker/hooks/plan-validator.sh IC_CODEX_NO_TOOLS_ARGS (a
// Commander test compares all three). The reviewer works only from the inline packet, so it gets
// no shell, file, image, app, agent, goal or web tool. Measured 2026-10-17 without these: it ran
// `cat` on a file outside its private cwd (tool-audit findings 2.4).
export const CODEX_NO_TOOLS_ARGS = [
  '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
  '-c', 'features.view_image=false', '-c', 'features.apps=false',
  '-c', 'features.browser_use=false', '-c', 'features.computer_use=false',
  '-c', 'features.multi_agent=false', '-c', 'features.image_generation=false',
  '-c', 'features.memories=false', '-c', 'features.plugins=false', '-c', 'features.hooks=false',
  '-c', 'features.goals=false', '-c', 'features.tool_suggest=false',
  '-c', 'agents.enabled=false', '-c', 'web_search="disabled"',
] as const;
```

In `runCodexAdvisorReview`'s review argv, change the line `        'exec',` to two lines, `        'exec',` and `        ...CODEX_NO_TOOLS_ARGS,`. `-s read-only`, `--ignore-user-config` and `--ignore-rules` stay unchanged.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/tools/advisor-review.test.ts > /private/tmp/claude-502/taf/t3-green-ts.log 2>&1; echo "green_ts=$?"; rg -n -e ' FAIL ' -e 'Tests  ' /private/tmp/claude-502/taf/t3-green-ts.log; npx tsc --noEmit > /private/tmp/claude-502/taf/t3-tsc.log 2>&1; echo "tsc=$?"; cat /private/tmp/claude-502/taf/t3-tsc.log; cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py -q > /private/tmp/claude-502/taf/t3-green-py.log 2>&1; echo "green_py=$?"; tail -n 1 /private/tmp/claude-502/taf/t3-green-py.log
```

Expected:
- `green_ts=0` with 0 failed and no FAIL lines.
- The `tsc` errors are exactly the Task 1 Step 8 baseline: `tsc=0` with an empty log if the baseline was clean. No error names `advisor-review.ts` or `advisor-review.test.ts`.
- `green_py=0` with 0 failed.

**Step 3: Rebuild the runtime bundle**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run bundle > /private/tmp/claude-502/taf/t3-bundle.log 2>&1; echo "bundle=$?"; cat /private/tmp/claude-502/taf/t3-bundle.log; git -C /Users/roberthyatt/Code/ironclaude diff --stat -- worker/mcp-servers/state-manager/dist/index.js; git -C /Users/roberthyatt/Code/ironclaude diff -U0 -- worker/mcp-servers/state-manager/dist/index.js; echo "agents_off=$(rg -c 'agents.enabled=false' /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js)"
```

Expected: `bundle=0`, `1 file changed`, and `agents_off=1`. The `-U0` hunks only add the `CODEX_NO_TOOLS_ARGS` array and its spread into the review argv. If any hunk changes anything else, STOP and report the bundle drift without staging the bundle.

**Step 4: Full state-manager suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf/t3-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf/t3-npm.log
```

Expected: `npm=0`, or the FAIL lines are exactly the baseline failing set recorded in Task 1 Step 8. No `advisor-review.test.ts` test fails.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/state-manager/src/tools/advisor-review.ts worker/mcp-servers/state-manager/src/tools/advisor-review.test.ts worker/mcp-servers/state-manager/dist/index.js commander/tests/test_fast_model.py
```

Expected: no output.

---

## Task 4: Claude children restricted, low effort, hook env strip (R4, R5) (depends on 3)

**Files:**
- Modify `commander/src/ironclaude/fast_model.py`: the `_ENV_STRIP` comment and tuple, lines 45-53, and `claude_fast_argv`, lines 96-101.
- Modify `commander/src/ironclaude/orchestrator_mcp.py`: `_claude_grader_argv`, lines 1057-1062.
- Modify `commander/tests/test_fast_model.py`: lines 40-45, plus one appended test.
- Modify `commander/tests/test_grader_routing.py`: line 45.
- Modify `worker/hooks/plan-validator.sh`: the `_call_fast_model` Claude arm, lines 174-176 before Task 2 (find them by the text `claude --model "$claude_model" --print`).
- Modify `worker/hooks/tests/test-fast-model-fallback.sh`: the claude stub (lines 23-37), lines 90-92 and 188-190, and one new block after line 92.

**Step 1: Commander tests (RED)**

In `test_fast_model.py`, `test_claude_argv_is_lean_and_session_free` becomes:

```python
def test_claude_argv_is_lean_and_session_free():
    assert fast_model.claude_fast_argv("claude", "haiku", SCHEMA) == [
        "claude", "-p", "--model", "haiku", "--restricted", "--effort", "low",
        "--tools", "", "--no-session-persistence",
        "--strict-mcp-config", "--settings", '{"disableAllHooks":true}',
        "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
    ]
```

Append to `test_fast_model.py`:

```python
def test_hook_claude_child_unsets_the_commander_strip_list():
    """One IronClaude: the hook's Claude child runs without the routing variables _claude_env strips."""
    import re
    assert re.findall(r"-u ([A-Z_]+)", _hook_text()) == list(fast_model._ENV_STRIP)


def test_claude_env_drops_inherited_effort(monkeypatch):
    """Design R5: an inherited CLAUDE_CODE_EFFORT_LEVEL beats --effort low (measured), so the fast child drops it."""
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "high")
    monkeypatch.setenv("IC_KEEP_PROBE", "kept")
    env = fast_model._claude_env()
    assert "CLAUDE_CODE_EFFORT_LEVEL" not in env
    assert env["IC_KEEP_PROBE"] == "kept"  # positive control: other variables still pass through
```

In `test_grader_routing.py` `test_claude_grader_argv_is_stable`, replace line 45, `    assert "--dangerously-skip-permissions" in argv`, with:

```python
    assert "--dangerously-skip-permissions" not in argv  # --restricted refuses bypassPermissions
    assert "--restricted" in argv
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t4-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t4-red.log; tail -n 1 /private/tmp/claude-502/taf/t4-red.log
```

Expected: `red=1`. FAILED is exactly `test_claude_argv_is_lean_and_session_free`, `test_hook_claude_child_unsets_the_commander_strip_list`, `test_claude_env_drops_inherited_effort` and `test_claude_grader_argv_is_stable`.

**Step 2: Commander argv and env (GREEN for the Commander)**

The `_ENV_STRIP` comment and tuple (lines 45-53) become:

```python
# Variables dropped from every Claude fast child (claude children only; codex authenticates with
# its own ChatGPT login and gets the env unmodified): the billing/routing strip shared with
# OrchestratorTools._GRADER_ENV_STRIP, plus CLAUDE_CODE_EFFORT_LEVEL, which a worker exports and
# which beats --effort low when inherited (measured 2026-10-17, design R5). The grader keeps its
# own list and sets its effort through _grader_env.
_ENV_STRIP = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_EFFORT_LEVEL",
)
```

`claude_fast_argv` becomes:

```python
def claude_fast_argv(path: str, model: str, schema: dict) -> list[str]:
    # --restricted drops the server-side advisor tool, user plugins, skills and auto-memory (the
    # child self-reported `advisor` without it; tool-audit findings 2.3). --effort low matches the
    # Codex child's model_reasoning_effort="low".
    return [
        path, "-p", "--model", model, "--restricted", "--effort", "low",
        "--tools", "", "--no-session-persistence",
        "--strict-mcp-config", "--settings", _DISABLE_HOOKS_SETTINGS,
        "--output-format", "json", "--json-schema", json.dumps(schema),
    ]
```

In `_claude_grader_argv`, replace the line `            "--dangerously-skip-permissions",` and the 5-line comment below it (from `# --dangerously-skip-permissions is required for a non-interactive run, so the` through `# the verdict still arrives through --json-schema structured output.`) with:

```python
            "--restricted",
            # --restricted (which refuses --dangerously-skip-permissions) ignores user, project and
            # local settings and drops the server-side advisor tool. The grader runs with NO tools
            # (--tools "", like the fast-model child): the prompt embeds worker-controlled log text,
            # so treat it as untrusted. --strict-mcp-config drops plugin MCP servers and
            # disableAllHooks keeps user/IronClaude hooks out; the verdict still arrives through
            # --json-schema structured output. Effort comes from _grader_env.
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t4-mid.log 2>&1; echo "mid=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t4-mid.log
```

Expected: `mid=1`. FAILED is exactly `test_hook_claude_child_unsets_the_commander_strip_list`, because the hook is not changed yet.

**Step 3: Hook test (RED)**

In `worker/hooks/tests/test-fast-model-fallback.sh`:
- In the `claude` stub heredoc, after the line `pwd -P > "$STUB_LOG_DIR/claude.cwd"`, insert:

```bash
for v in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_EFFORT_LEVEL IC_KEEP_PROBE; do printf '%s=%s\n' "$v" "${!v-unset}"; done > "$STUB_LOG_DIR/claude.env"
```

- In both exact Claude argv expectations (`claude isolation flags (exact argv)`, line 91, and `sonnet isolation flags (exact argv)`, line 189), change `|--print|--tools||` to `|--print|--restricted|--effort|low|--tools||`.
- Directly after the `claude isolation flags (exact argv)` assertion (after line 92), insert:

```bash

echo "=== Claude child runs without provider-routing variables or the worker's effort ==="
export ANTHROPIC_API_KEY=k ANTHROPIC_AUTH_TOKEN=t ANTHROPIC_BASE_URL=http://127.0.0.1:9 CLAUDE_CODE_USE_BEDROCK=1 CLAUDE_CODE_USE_VERTEX=1 CLAUDE_CODE_EFFORT_LEVEL=high IC_KEEP_PROBE=kept
run_cvl
assert_eq "routing vars stripped from the claude child" \
  "ANTHROPIC_API_KEY=unset|ANTHROPIC_AUTH_TOKEN=unset|ANTHROPIC_BASE_URL=unset|CLAUDE_CODE_USE_BEDROCK=unset|CLAUDE_CODE_USE_VERTEX=unset|CLAUDE_CODE_EFFORT_LEVEL=unset|IC_KEEP_PROBE=kept|" \
  "$(tr '\n' '|' < "$STUB_LOG_DIR/claude.env")"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_EFFORT_LEVEL IC_KEEP_PROBE
```

`IC_KEEP_PROBE=kept` is the positive control: a variable outside the strip list still reaches the child.

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf/t4-hook-red.log 2>&1; echo "rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf/t4-hook-red.log
```

Expected: `rc=1` and exactly three FAIL lines: `FAIL: claude isolation flags (exact argv)`, `FAIL: routing vars stripped from the claude child` and `FAIL: sonnet isolation flags (exact argv)`.

**Step 4: Change the hook (GREEN)**

In `plan-validator.sh` `_call_fast_model`, the Claude arm's two lines

```bash
      claude --model "$claude_model" --print --tools "" --no-session-persistence --strict-mcp-config \
      --settings '{"disableAllHooks":true}' --output-format json --json-schema "$schema" \
```

become:

```bash
      env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
      -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX -u CLAUDE_CODE_EFFORT_LEVEL \
      claude --model "$claude_model" --print --restricted --effort low --tools "" --no-session-persistence \
      --strict-mcp-config --settings '{"disableAllHooks":true}' --output-format json --json-schema "$schema" \
```

Directly above the `else` branch's `portable_timeout` line, add the comment `    # Same strip as commander fast_model._ENV_STRIP: routing vars (an Ollama worker exports ANTHROPIC_BASE_URL) and the worker's CLAUDE_CODE_EFFORT_LEVEL, which beats --effort low; env execs claude, so the killed PID is still the child.`

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py tests/test_fast_model_chain.py tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t4-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t4-green.log; tail -n 1 /private/tmp/claude-502/taf/t4-green.log; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "grader or Grader" > /private/tmp/claude-502/taf/t4-green-orch.log 2>&1; echo "orch=$?"; tail -n 1 /private/tmp/claude-502/taf/t4-green-orch.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf/t4-hook-green.log 2>&1; echo "hook=$?"; tail -n 1 /private/tmp/claude-502/taf/t4-hook-green.log
```

Expected: `green=0` and `orch=0`, each with 0 failed; `hook=0` with `Results: 68 passed, 0 failed`. The 4 timeout/kill assertions still pass, so `env` exec'd `claude` and the killed PID was the child.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/fast_model.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_fast_model.py commander/tests/test_grader_routing.py worker/hooks/plan-validator.sh worker/hooks/tests/test-fast-model-fallback.sh
```

Expected: no output.

---

## Task 5: Grader rubric in the prompt for both clients (R6) (depends on 4)

**Files:** Modify `commander/src/ironclaude/orchestrator_mcp.py`: `_call_grader` after the profile block (lines 1288-1294), plus a new `_grader_rubric` method directly before `_call_grader`. Modify `commander/tests/test_grader_routing.py` (append).

**Step 1: Tests (RED)**

Append to `commander/tests/test_grader_routing.py`:

```python
# ── Grader rubric in the prompt (design 2026-10-17 tool-audit-fixes R6) ──

RUBRIC = "# Grader Instructions\n\nRUBRIC-MARK-5c1 Robert's Three Laws of Testing\n"


def _with_grader_home(tools, tmp_path, rubric):
    home = tmp_path / "grader-home"
    home.mkdir()
    if rubric is not None:
        (home / "CLAUDE.md").write_text(rubric)
    tools._grader_home = str(home)
    return tools


def test_claude_grader_system_prompt_starts_with_rubric(tmp_path):
    from ironclaude.communication_profiles import apply_communication_profile
    tools = _with_grader_home(_tools(tmp_path), tmp_path, RUBRIC)
    seen = {}

    def _run(argv, **kwargs):
        with open(argv[argv.index("--system-prompt-file") + 1]) as f:
            seen["system"] = f.read()
        seen["input"] = kwargs["input"]
        return _fake_result({"grade": "A", "approved": True, "feedback": "ok"})

    with patch.object(omcp.subprocess, "run", side_effect=_run):
        assert tools._call_grader("sys", "user")["grade"] == "A"
    assert seen["system"] == RUBRIC.strip() + "\n\n" + apply_communication_profile("claude_grader", "sys")
    assert seen["input"] == "user"


def test_claude_batch_grader_system_prompt_starts_with_rubric(tmp_path):
    from ironclaude.communication_profiles import apply_communication_profile
    tools = _with_grader_home(_tools(tmp_path), tmp_path, RUBRIC)
    seen = {}
    verdicts = [{"grade": "A", "approved": True, "feedback": "ok"}]

    def _run(argv, **kwargs):
        with open(argv[argv.index("--system-prompt-file") + 1]) as f:
            seen["system"] = f.read()
        return _fake_result({"verdicts": verdicts})

    with patch.object(omcp.subprocess, "run", side_effect=_run):
        assert tools._call_grader("sys", "user", batch=True) == verdicts
    assert seen["system"].startswith(RUBRIC.strip() + "\n\n")


def test_codex_grader_stdin_starts_with_rubric(tmp_path, monkeypatch):
    from ironclaude.communication_profiles import apply_communication_profile
    tools = _with_grader_home(_codex_enabled_cfg(tmp_path, clients=("codex",)), tmp_path, RUBRIC)
    _codex_avail(monkeypatch)
    with patch.object(omcp.subprocess, "run", return_value=_codex_proc("codex_grader_verdict.jsonl")) as run:
        assert tools._call_grader("sys", "user")["grade"] == "B"
    assert run.call_args.kwargs["input"] == (
        RUBRIC.strip() + "\n\n" + apply_communication_profile("codex_grader", "sys") + "\n\nuser")


def test_missing_rubric_adds_nothing(tmp_path, monkeypatch):
    """Positive control: no CLAUDE.md in the grader home is not an error and adds nothing."""
    from ironclaude.communication_profiles import apply_communication_profile
    tools = _with_grader_home(_codex_enabled_cfg(tmp_path, clients=("codex",)), tmp_path, None)
    _codex_avail(monkeypatch)
    with patch.object(omcp.subprocess, "run", return_value=_codex_proc("codex_grader_verdict.jsonl")) as run:
        assert tools._call_grader("sys", "user")["grade"] == "B"
    assert run.call_args.kwargs["input"] == apply_communication_profile("codex_grader", "sys") + "\n\nuser"
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t5-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t5-red.log; tail -n 1 /private/tmp/claude-502/taf/t5-red.log
```

Expected: `red=1`. FAILED is exactly `test_claude_grader_system_prompt_starts_with_rubric`, `test_claude_batch_grader_system_prompt_starts_with_rubric` and `test_codex_grader_stdin_starts_with_rubric`. `test_missing_rubric_adds_nothing` passes.

**Step 2: Implement (GREEN)**

Add this method directly before `def _call_grader(`:

```python
    def _grader_rubric(self) -> str:
        """The grader-home rubric (``CLAUDE.md``, synced and substituted at Commander start-up by
        main.py), stripped; "" when the file is missing or unreadable. _call_grader prepends it to
        the system prompt for both clients, so the rubric does not depend on a client loading
        CLAUDE.md or AGENTS.md from its cwd."""
        try:
            with open(os.path.join(self._grader_home, "CLAUDE.md"), encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""
```

In `_call_grader`, directly after the block

```python
        except CommunicationProfileError as exc:
            return self._grader_failure(batch, str(exc))
```

insert:

```python
        # Rubric first, then the profiled prompt: Claude reads it from --system-prompt-file,
        # Codex from the stdin prompt (design 2026-10-17 tool-audit-fixes R6).
        rubric = self._grader_rubric()
        if rubric:
            profiled_system_prompt = f"{rubric}\n\n{profiled_system_prompt}"
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_grader_routing.py -q > /private/tmp/claude-502/taf/t5-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t5-green.log; tail -n 1 /private/tmp/claude-502/taf/t5-green.log; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "grader or Grader" > /private/tmp/claude-502/taf/t5-green-orch.log 2>&1; echo "orch=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t5-green-orch.log; tail -n 1 /private/tmp/claude-502/taf/t5-green-orch.log
```

Expected: `green=0` and `orch=0`, each with 0 failed. `TestCallGraderSubprocess`'s two `loads_exact_skill_before_untrusted_input` tests still pass: they assert with `in` and `index`, and their grader home has no `CLAUDE.md`.

**Step 3: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_grader_routing.py
```

Expected: no output.

---

## Task 6: Newest installed Codex model for every local role (R7) (depends on 5)

Operator decision 2026-10-17: "Local only, remote keeps config". This Mac's roles (graders, fast children, Brain, local workers) use the newest model this Mac's Codex lists. Remote workers keep the configured slug.

**Files:**
- Modify `commander/src/ironclaude/provider_config.py`: imports; a new function after `CODEX_MODEL_RE` (line 32); `ProviderConfig.model_for` and `advisor_model_for` (lines 71-90).
- Modify `commander/src/ironclaude/provider_router.py`: the model lookup in `resolve` (lines 81-87).
- Modify `commander/src/ironclaude/codex_brain_client.py`: an import, plus `_resolve_model` (lines 262-265).
- Modify `commander/src/ironclaude/fast_model.py`: an import, plus `fast_model_clients` (line 90).
- Modify `commander/tests/conftest.py`: `_redirect_home`, lines 255-274.
- Modify `commander/tests/test_provider_config.py` (append).
- Modify `commander/tests/test_provider_router.py` (append).
- Modify `commander/tests/test_codex_brain_client.py` (append to `class TestModelWiring`, after line 492).
- Modify `commander/tests/test_fast_model.py` (append).

**Step 1: Tests (RED)**

Append to `commander/tests/test_provider_config.py` (each new import sits inside its test, so a missing name fails one test, not collection):

```python
# ── Newest installed Codex model (design 2026-10-17 tool-audit-fixes R7) ──

def _codex_cache(directory, slugs):
    import json
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "models_cache.json").write_text(json.dumps(
        {"models": [{"slug": slug} for slug in slugs] + [{"slug": None}, {}]}))
    return directory


def test_codex_model_for_uses_newest_installed_by_numeric_version(base_config, tmp_path, monkeypatch):
    home = _codex_cache(tmp_path / "codexhome", [
        "gpt-5.9-luna", "gpt-5.10-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-reserve", "codex-auto-review"])
    monkeypatch.setenv("CODEX_HOME", str(home))
    cfg = parse_provider_config(base_config())
    assert cfg.model_for("codex", "haiku") == "gpt-5.10-luna"  # 5.10 > 5.9 numerically
    assert cfg.model_for("codex", "opus") == "gpt-6.1-sol"
    assert cfg.model_for("codex", "sonnet") == "gpt-5.6-terra"  # no Terra installed: configured slug
    assert cfg.advisor_model_for("codex", "claude-opus", "opus") == "gpt-6.1-sol"
    assert cfg.model_for("claude", "haiku") == "haiku"  # Claude is untouched
    # Remote hosts keep the configured slug even though this Mac has a newer model installed.
    assert cfg.model_for("codex", "opus", host="box-1") == "gpt-5.6-sol"
    assert cfg.advisor_model_for("codex", "claude-opus", "opus", host="box-1") == "gpt-5.6-sol"
    assert cfg.model_for("codex", "opus", host="local") == "gpt-6.1-sol"


def test_newest_installed_codex_model_falls_back_without_a_usable_cache(tmp_path, monkeypatch):
    from ironclaude.provider_config import newest_installed_codex_model
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing"))
    assert newest_installed_codex_model("haiku", "gpt-5.6-luna") == "gpt-5.6-luna"
    bad = tmp_path / "bad"
    bad.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(bad))
    for text in ("{not json", "[1, 2]", '{"models": "x"}', '{"models": [{"slug": "gpt-6-sol"}]}'):
        (bad / "models_cache.json").write_text(text)
        assert newest_installed_codex_model("haiku", "gpt-5.6-luna") == "gpt-5.6-luna", text
    assert newest_installed_codex_model("unknown-tier", "x-model") == "x-model"


def test_newest_installed_codex_model_defaults_to_home_dot_codex(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from ironclaude.provider_config import newest_installed_codex_model
    monkeypatch.delenv("CODEX_HOME", raising=False)
    _codex_cache(Path(os.environ["HOME"]) / ".codex", ["gpt-6-luna", "gpt-5.6-luna"])
    assert newest_installed_codex_model("haiku", "gpt-5.6-luna") == "gpt-6-luna"


def test_codex_home_is_an_empty_dir_for_every_test(base_config):
    """conftest _redirect_home points CODEX_HOME at an empty directory, so a developer's installed
    Codex models never change what the suite resolves."""
    import os
    home = os.environ["CODEX_HOME"]
    assert os.path.isdir(home) and os.listdir(home) == []
    assert parse_provider_config(base_config()).model_for("codex", "haiku") == "gpt-5.6-luna"
```

Append to `commander/tests/test_provider_router.py` (it reuses that file's `setup_router`, `cap` and `record`):

```python
# ── Newest installed Codex model, local hosts only (design 2026-10-17 tool-audit-fixes R7) ──

def test_codex_model_is_newest_installed_only_on_the_local_host(tmp_path, base_config, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "models_cache.json").write_text('{"models": [{"slug": "gpt-6.1-sol"}, {"slug": "gpt-6-sol"}]}')
    monkeypatch.setenv("CODEX_HOME", str(home))
    router, state, registry = setup_router(tmp_path, base_config, dual=True)
    state.set_current_client("worker", "codex")
    state.set_current_client("advisor", "codex")
    record(
        registry,
        cap("codex", "worker", "opus"),
        cap("codex", "worker", "opus", host="box-1"),
        cap("codex", "advisor", "opus"),
        cap("codex", "advisor", "opus", host="box-1"),
    )
    local = router.resolve("worker", "opus", ["local"])
    remote = router.resolve("worker", "opus", ["box-1"])
    assert (local.client, local.host, local.model) == ("codex", "local", "gpt-6.1-sol")
    assert (remote.client, remote.host, remote.model) == ("codex", "box-1", "gpt-5.6-sol")
    assert router.resolve("advisor", "opus", ["local"], advisor_worker_type="claude-opus").model == "gpt-6.1-sol"
    assert router.resolve("advisor", "opus", ["box-1"], advisor_worker_type="claude-opus").model == "gpt-5.6-sol"
```

Append inside `class TestModelWiring` in `commander/tests/test_codex_brain_client.py` (after `test_model_wiring_preserves_sandbox_and_approval`):

```python
    def test_brain_model_is_newest_installed_with_table_fallback(self, tmp_path, monkeypatch):
        """R7 (local only): the Codex Brain runs on this Mac, so it uses the newest installed model
        for its tier, with the _CODEX_TIER_MODELS value as fallback."""
        home = tmp_path / "codexhome"
        home.mkdir()
        (home / "models_cache.json").write_text(json.dumps(
            {"models": [{"slug": "gpt-6.1-sol"}, {"slug": "gpt-6-sol"}, {"slug": "gpt-6-luna"}]}))
        monkeypatch.setenv("CODEX_HOME", str(home))
        assert CodexBrainClient(model="opus")._resolve_model() == "gpt-6.1-sol"
        assert CodexBrainClient(model="gpt-5.6-sol")._resolve_model() == "gpt-6.1-sol"
        assert CodexBrainClient(model="haiku")._resolve_model() == "gpt-6-luna"
        assert CodexBrainClient(model="sonnet")._resolve_model() == "gpt-5.6-terra"  # no Terra installed
        assert 'model="gpt-6.1-sol"' in CodexBrainClient(model="opus")._app_server_argv()
```

The existing `TestModelWiring` tests keep passing after the change because conftest's `CODEX_HOME` is empty, so every lookup falls back to the table.

Append to `commander/tests/test_fast_model.py`:

```python
def test_codex_fast_clients_use_newest_installed_model(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(
        {"models": [{"slug": "gpt-6-luna"}, {"slug": "gpt-5.10-terra"}, {"slug": "gpt-5.9-terra"}]}))
    monkeypatch.setenv("CODEX_HOME", str(home))
    cfg = _cfg_classes(preferred="codex", clients=("codex", "claude"), codex_enabled=True)
    assert fast_model.fast_model_clients(cfg) == [
        ("codex", "/bin/codex-x", "gpt-6-luna"), ("claude", "/bin/claude-x", "haiku")]
    assert fast_model.fast_model_clients(cfg, "sonnet") == [
        ("codex", "/bin/codex-x", "gpt-5.10-terra"), ("claude", "/bin/claude-x", "sonnet")]
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_provider_config.py tests/test_provider_router.py tests/test_codex_brain_client.py tests/test_fast_model.py -q > /private/tmp/claude-502/taf/t6-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t6-red.log; tail -n 1 /private/tmp/claude-502/taf/t6-red.log
```

Expected: `red=1`. FAILED is exactly these 7:
- `test_codex_model_for_uses_newest_installed_by_numeric_version`
- `test_newest_installed_codex_model_falls_back_without_a_usable_cache` (ImportError)
- `test_newest_installed_codex_model_defaults_to_home_dot_codex` (ImportError)
- `test_codex_home_is_an_empty_dir_for_every_test` (KeyError: `_redirect_home` deletes `CODEX_HOME`)
- `test_codex_model_is_newest_installed_only_on_the_local_host` (the local model is `gpt-5.6-sol`)
- `TestModelWiring::test_brain_model_is_newest_installed_with_table_fallback`
- `test_codex_fast_clients_use_newest_installed_model`

**Step 2: Resolver and call sites (GREEN except the conftest test)**

In `provider_config.py`, add `import json` and `import os` beside `import re`. Directly after the `CODEX_MODEL_RE = ...` line, add:

```python


def newest_installed_codex_model(tier: str, fallback: str) -> str:
    """Newest installed ``gpt-<version>-<word>`` for the tier's Codex word (haiku→luna,
    sonnet→terra, opus→sol, fable→astra) from ``$CODEX_HOME/models_cache.json`` (default
    ``~/.codex``), ordered by numeric version like plan-validator.sh ``_newest_codex_model`` and
    advisor-review.ts ``newestAtTier``. ``fallback`` (the configured slug) when the cache is
    missing, unreadable or has no model for the tier."""
    word = CODEX_TIER_WORDS.get(tier)
    if word is None:
        return fallback
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    try:
        with open(os.path.join(home, "models_cache.json"), encoding="utf-8") as handle:
            models = json.load(handle).get("models")
    except (OSError, ValueError, AttributeError):
        return fallback
    best = None
    for entry in models if isinstance(models, list) else ():
        slug = entry.get("slug") if isinstance(entry, dict) else None
        match = CODEX_MODEL_RE.fullmatch(slug) if isinstance(slug, str) else None
        if match is None or match.group(2) != word:
            continue
        version = tuple(int(part) for part in match.group(1).split("."))
        if best is None or version > best[0]:
            best = (version, slug)
    return best[1] if best else fallback
```

Replace `ProviderConfig.model_for` and `advisor_model_for` (lines 71-90) with:

```python
    def model_for(self, client: str, tier: str, role: str | None = None, *, host: str = "local") -> str:
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
        # One Codex model rule for this Mac (design 2026-10-17 tool-audit-fixes R7, operator
        # decision "Local only, remote keeps config"): a local Codex role runs the newest model
        # this Mac's Codex lists, else the configured slug. A remote host keeps the configured
        # slug, because its Codex may not have this Mac's newest model.
        if client == "codex" and host == "local":
            return newest_installed_codex_model(tier, model)
        return model

    def advisor_model_for(
        self, client: str, worker_type: str, tier: str, *, host: str = "local"
    ) -> str:
        if client == "claude":
            override = self.advisor_worker_models.get(worker_type)
            if override is not None:
                return override
        return self.model_for(client, tier, "advisor", host=host)
```

In `provider_router.py` `resolve`, the model lookup becomes:

```python
                model = (
                    self._config.advisor_model_for(
                        client, advisor_worker_type, effective_tier, host=host
                    )
                    if role == "advisor" and advisor_worker_type is not None
                    else self._config.model_for(client, effective_tier, role, host=host)
                )
```

In `codex_brain_client.py`, add `from ironclaude.provider_config import CODEX_MODEL_RE, CODEX_TIER_WORDS, newest_installed_codex_model` beside the existing `ironclaude` imports. `provider_config` imports nothing from `ironclaude`, so this adds no import cycle. Replace `_resolve_model` with:

```python
    def _resolve_model(self) -> str:
        """Map a tier name (haiku/sonnet/opus/fable) to its codex model; a value already in
        codex-model form is kept. The Brain runs on this Mac, so a gpt-<version>-<word> result
        is then replaced by the newest installed model for that word (design 2026-10-17
        tool-audit-fixes R7), with the table/configured value as fallback."""
        configured = _CODEX_TIER_MODELS.get(self._model, self._model)
        match = CODEX_MODEL_RE.fullmatch(configured) if isinstance(configured, str) else None
        if match is None:
            return configured
        tier = {word: name for name, word in CODEX_TIER_WORDS.items()}[match.group(2)]
        return newest_installed_codex_model(tier, configured)
```

The effort lookup in `_app_server_argv` still keys on `self._model`, so it does not change.

In `fast_model.py`, add `from ironclaude.provider_config import newest_installed_codex_model` after `from collections.abc import Mapping`. `provider_config` imports nothing from `ironclaude`, so `grader.py` still never imports `orchestrator_mcp`. In `fast_model_clients`, replace

```python
            out.append((name, client.get("path") or name, client["models"][tier]))
```

with

```python
            model = client["models"][tier]
            if name == "codex":
                model = newest_installed_codex_model(tier, model)
            out.append((name, client.get("path") or name, model))
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_provider_config.py tests/test_provider_router.py tests/test_codex_brain_client.py tests/test_fast_model.py -q > /private/tmp/claude-502/taf/t6-mid.log 2>&1; echo "mid=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t6-mid.log
```

Expected: `mid=1`. FAILED is exactly `test_codex_home_is_an_empty_dir_for_every_test`.

**Step 3: Conftest isolation (GREEN)**

In `commander/tests/conftest.py` `_redirect_home`:
- In the docstring, replace `CODEX_HOME is cleared so Codex config resolves under the fake home too.` with `CODEX_HOME points at an empty per-test directory.`
- Replace the 3 lines from `    # R14: CodexBrainClient reads $CODEX_HOME/config.toml (else ~/.codex); a developer's` through `    monkeypatch.delenv("CODEX_HOME", raising=False)` with:

```python
    # R14: CodexBrainClient reads $CODEX_HOME/config.toml (else ~/.codex); a developer's
    # CODEX_HOME must not leak their real Codex config. It points at an empty directory, so the
    # Codex model resolver (provider_config.newest_installed_codex_model) finds no
    # models_cache.json and every test resolves the configured slugs.
    codex_home = tmp_path / "ic-empty-codex-home"
    codex_home.mkdir(exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_provider_config.py tests/test_provider_router.py tests/test_fast_model.py tests/test_fast_model_chain.py tests/test_grader_routing.py tests/test_grader_router.py tests/test_codex_brain_client.py tests/test_codex_brain_wiring.py tests/test_worker_adapter.py tests/test_worker_handoff.py tests/test_orchestrator_mcp.py -q > /private/tmp/claude-502/taf/t6-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t6-green.log; tail -n 1 /private/tmp/claude-502/taf/t6-green.log
```

Expected: `green=0` with 0 failed. Every existing test that expects a configured `gpt-5.6-*` slug still passes under the empty `CODEX_HOME`. That includes `TestModelWiring`'s pass-through tests and `TestRemoteProviderLaunching`.

**Step 4: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/provider_config.py commander/src/ironclaude/provider_router.py commander/src/ironclaude/codex_brain_client.py commander/src/ironclaude/fast_model.py commander/tests/conftest.py commander/tests/test_provider_config.py commander/tests/test_provider_router.py commander/tests/test_codex_brain_client.py commander/tests/test_fast_model.py
```

Expected: no output.

---

## Task 7: Small fixes: Explore reviewer, strict hook schema, Codex resume flags (R8) (depends on 6)

**Files:**
- Modify `worker/skills/advisor-fallback/SKILL.md:37`.
- Modify `commander/tests/test_advisor_fallback_directive.py` (append).
- Modify `worker/hooks/plan-validator.sh`: a new `_strict_codex_schema` before `_call_fast_model`; the Codex schema line (line 162 before Task 2).
- Modify `worker/hooks/tests/test-fast-model-fallback.sh`: one new assertion after the line `assert_eq "codex prompt delivered on stdin" ...` (line 139 before Task 4).
- Modify `commander/tests/test_fast_model.py` (append).
- Modify `commander/src/ironclaude/orchestrator_mcp.py`: the `provider_config` import (line 54) and `resume_session`'s Codex command (lines 6828-6837).
- Modify `commander/tests/test_orchestrator_mcp.py`: the codex case of `test_success_uses_exact_client_and_persists_native_identity` (lines 10613-10626).
- Modify `worker/config-schema/llm-backend.md`, lines 232-233. No test: reference doc.

**Step 1: Tests (RED)**

Append to `commander/tests/test_advisor_fallback_directive.py`:

```python
def test_claude_advisor_reviewer_is_a_read_only_explore_agent():
    """Design 2026-10-17 tool-audit-fixes R8: the report-only Claude reviewer is the Explore agent type."""
    text = _read("worker/skills/advisor-fallback/SKILL.md")
    assert 'subagent_type="Explore"' in text
    assert "general-purpose" not in text
```

Append to `commander/tests/test_fast_model.py`:

```python
def test_hook_strict_schema_matches_python():
    """One IronClaude: the hook's Codex schema transform equals fast_model.strict_codex_schema."""
    import pathlib
    hook = pathlib.Path(__file__).resolve().parents[2] / "worker" / "hooks" / "plan-validator.sh"
    proc = subprocess.run(["bash", "-c", 'source "$0" && _strict_codex_schema', str(hook)],
                          input=json.dumps(STRICT_IN), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == fast_model.strict_codex_schema(STRICT_IN) == STRICT_OUT
```

In `test_orchestrator_mcp.py`, the codex case of `test_success_uses_exact_client_and_persists_native_identity`'s parametrize changes in two places:
- Its `required` tuple becomes `("exec '/Applications/Codex App/codex' " f"resume {UUID} --model gpt-5.6-sol --no-daemon --dangerously-bypass-approvals-and-sandbox",)`.
- Its `forbidden` tuple drops the `"--model",` entry and keeps `"CLAUDE_CODE_EFFORT_LEVEL"`, `"--resume"` and `"--dangerously-skip-permissions"`.

The test's `tools._config` has no `models` and `CODEX_HOME` is empty, so the slug is the `EXPECTED_MODELS` opus fallback, `gpt-5.6-sol` (default `worker_type="claude-opus"` → tier `opus`).

In `worker/hooks/tests/test-fast-model-fallback.sh`, directly after the line `assert_eq "codex prompt delivered on stdin" "grade this" "$(cat "$STUB_LOG_DIR/codex.stdin")"`, insert:

```bash
NESTED='{"type":"object","properties":{"ok":{"type":"boolean"},"note":{"type":"string"},"opts":{"type":"array","items":{"type":"object","properties":{"v":{"type":"string"},"w":{"enum":["a","b"]}},"required":["v"]}}},"required":["ok","opts"]}'
reset_logs; call_validation_llm "grade this" "$NESTED" > "$WORK/out"
assert_eq "codex schema is strict at every level" \
  "$(printf '%s' '{"type":"object","properties":{"ok":{"type":"boolean"},"note":{"type":["string","null"]},"opts":{"type":"array","items":{"type":"object","properties":{"v":{"type":"string"},"w":{"enum":["a","b",null]}},"required":["v","w"],"additionalProperties":false}}},"required":["ok","note","opts"],"additionalProperties":false}' | jq -S -c .)" \
  "$(jq -S -c . "$STUB_LOG_DIR/codex.schema")"
```

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py tests/test_fast_model.py tests/test_orchestrator_mcp.py -q -k "read_only_explore_agent or hook_strict_schema_matches_python or test_success_uses_exact_client_and_persists_native_identity" > /private/tmp/claude-502/taf/t7-red.log 2>&1; echo "red=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t7-red.log; tail -n 1 /private/tmp/claude-502/taf/t7-red.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf/t7-hook-red.log 2>&1; echo "rc=$?"; rg -n '^FAIL' /private/tmp/claude-502/taf/t7-hook-red.log
```

Expected:
- `red=1`, with `3 failed, 1 passed`. FAILED is exactly `test_claude_advisor_reviewer_is_a_read_only_explore_agent`, `test_hook_strict_schema_matches_python`, and the codex case of `test_success_uses_exact_client_and_persists_native_identity`.
- `rc=1` with exactly one FAIL line, `FAIL: codex schema is strict at every level`.

**Step 2: Implement (GREEN)**

`SKILL.md` line 37: replace `` Dispatch a fresh, blind subagent with the `Agent` tool (`subagent_type="general-purpose"`, `` with `` Dispatch a fresh, blind subagent with the `Agent` tool (`subagent_type="Explore"`, a read-only agent type with no Edit or Write tool, ``.

`plan-validator.sh`: directly before the comment block `# Run one fast-model child (Haiku or Sonnet class) under its own cap.`, insert:

```bash
# Strict copy of a JSON schema (stdin) for `codex exec --output-schema`, mirroring commander
# fast_model.strict_codex_schema (a Commander test compares the two): every object node gets
# additionalProperties:false and lists every property in required; a formerly optional property
# becomes nullable. Recurses through properties, items, anyOf and oneOf.
_strict_codex_schema() {
  jq -c '
    def nullable:
      (if (.type | type) == "string" and .type != "null" then .type = [.type, "null"]
       elif (.type | type) == "array" and (.type | any(. == "null") | not) then .type += ["null"]
       else . end)
      | (if (.enum | type) == "array" and (.enum | any(. == null) | not) then .enum += [null] else . end);
    def strict:
      if type != "object" then .
      else
        (if (.items | type) == "object" then .items |= strict else . end)
        | (if (.anyOf | type) == "array" then .anyOf |= map(strict) else . end)
        | (if (.oneOf | type) == "array" then .oneOf |= map(strict) else . end)
        | (if (.properties | type) == "object" then
             ((.required // []) | if type == "array" then . else [] end) as $req
             | .properties |= with_entries(.key as $k
                 | .value |= (strict | if ($req | any(. == $k)) then . else nullable end))
             | .required = (.properties | keys_unsorted)
           else . end)
        | (if (.properties | type) == "object" or .type == "object" then .additionalProperties = false else . end)
      end;
    strict'
}

```

In `_call_fast_model`'s Codex arm, replace `    printf '%s' "$schema" | jq -c '. + {additionalProperties: false}' > "$work/schema.json"` with `    printf '%s' "$schema" | _strict_codex_schema > "$work/schema.json"`.

`orchestrator_mcp.py`:
- Line 54 becomes `from ironclaude.provider_config import provider_config_from_commander, _semantic_tier, ProviderConfigError, EXPECTED_MODELS, newest_installed_codex_model`.
- In `resume_session`, the `else:` (codex) branch becomes:

```python
        else:
            codex_cfg = (
                self._config.get("providers", {}).get("clients", {})
                .get("codex", {})
            )
            codex_path = codex_cfg.get("path", "codex")
            # Same flags as the fresh spawn (_codex_worker_command) and the handoff resume:
            # an explicit model (newest installed, else configured) and --no-daemon.
            tier = _WORKER_TYPE_TIER.get(worker_type, "opus")
            model = newest_installed_codex_model(
                tier, codex_cfg.get("models", {}).get(tier) or EXPECTED_MODELS["codex"][tier])
            cmd = (
                f"{env_prefix}exec {shlex.quote(codex_path)} "
                f"resume {shlex.quote(session_id)} "
                f"--model {shlex.quote(model)} --no-daemon "
                f"--dangerously-bypass-approvals-and-sandbox"
            )
```

`resume_session` does not call `_provider_router()`, which keeps `tools._provider_router.assert_not_called()` true in `TestResumeSession`.

Host rule (operator decision "Local only, remote keeps config"): `resume_session` is local-only. It takes no machine argument, launches with `self.tmux.spawn_session(target, cmd, cwd=repo)` with no SSH host, and calls `_wait_for_ready(..., ssh_host=None)` (lines 6788-6852). So the local resolver applies unconditionally. The updated parametrized test pins the fallback path: `gpt-5.6-sol` under the empty `CODEX_HOME`.

Fresh worker spawns need no code change here. Local spawns resolve through `router.resolve("worker", tier, ["local"])` (line 613), and remote spawns through `router.resolve("worker", tier, [machine_cfg.host])` (line 644), so Task 6's host-aware `model_for` covers both. Task 6's `test_codex_model_is_newest_installed_only_on_the_local_host` proves the split. The handoff resume gets its model from `_target`, which refuses any host other than `"local"` (`worker_handoff.py:269-271`).

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_advisor_fallback_directive.py tests/test_fast_model.py tests/test_activation_client_parity.py -q > /private/tmp/claude-502/taf/t7-green.log 2>&1; echo "green=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t7-green.log; tail -n 1 /private/tmp/claude-502/taf/t7-green.log; PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k "TestResumeSession" > /private/tmp/claude-502/taf/t7-green-resume.log 2>&1; echo "resume=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/t7-green-resume.log; tail -n 1 /private/tmp/claude-502/taf/t7-green-resume.log; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/taf/t7-hook-green.log 2>&1; echo "hook=$?"; tail -n 1 /private/tmp/claude-502/taf/t7-hook-green.log
```

Expected: `green=0` and `resume=0`, each with 0 failed; `hook=0` with `Results: 69 passed, 0 failed`. The two flat-schema assertions, `codex schema file carries the caller schema` and `codex sonnet schema is strict`, still pass, because for a flat all-required schema the full transform equals the old top-level one.

**Step 3: Reference doc**

In `worker/config-schema/llm-backend.md`:

Line 232: replace `Commander: the \`grader\` role's client order, using each client's configured \`models.haiku\`, which for Codex is \`providers.clients.codex.models.haiku\` (\`gpt-5.6-luna\` by default). The difference is deliberate: the Commander is config-driven.` with:

> Commander: the `grader` role's client order. Claude uses `models.haiku`; Codex uses the same newest-installed `gpt-*-luna` rule, falling back to `providers.clients.codex.models.haiku` (`gpt-5.6-luna` by default), so the hook and the Commander pick the same model.

Line 233: replace the whole bullet with:

> - **The isolation flags.**
>   - **Claude:** `--restricted --effort low --tools "" --no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true}'`, cwd `/tmp`. The hook also unsets `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and `CLAUDE_CODE_EFFORT_LEVEL`, as the Commander does; an inherited `CLAUDE_CODE_EFFORT_LEVEL` would override `--effort low`.
>   - **Codex:** `--ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only` and `model_reasoning_effort="low"`. The `--output-schema` is fully strict: every object is closed and every property required, and formerly optional properties become nullable. Codex also gets the shared no-tools set `fast_model.CODEX_NO_TOOLS_ARGS`: `-c features.<name>=false` for shell_tool, unified_exec, view_image, apps, browser_use, computer_use, multi_agent, image_generation, memories, plugins, hooks, goals and tool_suggest, plus `-c agents.enabled=false` and `-c web_search="disabled"`. The Codex grader and the Codex advisor broker use the same set.
>   - **Still on:** `unified_exec` stays on whatever the flag says (measured 2026-10-17).

```bash
rg -n -e 'CODEX_NO_TOOLS_ARGS' -e 'newest-installed' /Users/roberthyatt/Code/ironclaude/worker/config-schema/llm-backend.md
```

Expected: exactly 2 matching lines: the Commander mapping bullet (`newest-installed`) and the Codex isolation-flags line (`CODEX_NO_TOOLS_ARGS`).

**Step 4: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/advisor-fallback/SKILL.md commander/tests/test_advisor_fallback_directive.py worker/hooks/plan-validator.sh worker/hooks/tests/test-fast-model-fallback.sh commander/tests/test_fast_model.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py worker/config-schema/llm-backend.md
```

Expected: no output.

---

## Task 8: Probes after the change, regression, findings and staging (depends on 7)

**Files:** Modify `docs/plans/2026-10-17-tool-audit-fixes-findings.md`. Stage the design, this plan, its `.plan.json` and the findings.

**Step 1: Codex self-reports after**

```bash
for r in codex-grader codex-fast codex-hook codex-broker; do /Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/selfreport.py "$r" > "/private/tmp/claude-502/taf/a-$r.log" 2>&1; echo "$r probe=$?"; cat "/private/tmp/claude-502/taf/a-$r.log"; echo "$r banned_lines=$(rg -c '^answer=.*(collaboration|multi_agent|goal|mcp__codex_apps__)' "/private/tmp/claude-502/taf/a-$r.log")"; done
```

Expected:
- Every role prints `rc=0` and a non-null `answer=`; `codex-hook` prints `hook_rc=0`. A non-zero rc or null answer means the new pairs broke a child: STOP and report.
- Every `banned_lines=` is empty.
- The `codex-broker` `argv=` line contains `agents.enabled=false`, which proves the probe read the shipped TS constant.

**Step 2: Broker sentinel after**

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/selfreport.py codex-broker /private/tmp/claude-502/taf/action-prompt.txt > /private/tmp/claude-502/taf/a-broker-action.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf/a-broker-action.log; echo "token_lines=$(rg -c '^answer=.*TAF-SENTINEL-4b19e2' /private/tmp/claude-502/taf/a-broker-action.log)"
```

Expected: `rc=0`, `token_lines=` empty, and no `command_execution` in `tool_items=`. If the token appears, STOP and report.

**Step 3: Claude self-reports after (structured output still arrives, no advisor)**

```bash
for r in claude-grader claude-fast claude-hook; do /Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/selfreport.py "$r" > "/private/tmp/claude-502/taf/a-$r.log" 2>&1; echo "$r probe=$?"; cat "/private/tmp/claude-502/taf/a-$r.log"; echo "$r advisor_lines=$(rg -c -i '^answer=.*advisor' "/private/tmp/claude-502/taf/a-$r.log")"; done
```

Expected:
- Every role prints `rc=0` and a non-null `answer=`; `claude-hook` prints `hook_rc=0`. This is R4's measured requirement: `structured_output` under `--restricted` and `--effort low`. A non-zero rc or null answer means the CLI rejected a flag: STOP and report.
- Every `advisor_lines=` is empty.

**Step 4: Hook env after**

```bash
bash /private/tmp/claude-502/taf/hook_env.sh > /private/tmp/claude-502/taf/a-hook-env.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf/a-hook-env.log
```

Expected: a JSON line with `answer`, then `hook_rc=0`. The child no longer inherits `ANTHROPIC_BASE_URL`.

**Step 4b: Effort precedence (record, design R5)**

The hook and fast children run with `--no-session-persistence`, so their effort cannot be read back. This probe instead records the CLI rule that makes the strip necessary, using a session-persisting child in a fresh directory.

```bash
mkdir /private/tmp/claude-502/taf/a-effort-probe && cd /private/tmp/claude-502/taf/a-effort-probe && env CLAUDE_CODE_EFFORT_LEVEL=high claude -p --model haiku --effort low --tools "" --strict-mcp-config --settings '{"disableAllHooks":true}' "Reply with the single word ok. probe=envhigh-flaglow" > envhigh-flaglow.out 2>&1; echo "envhigh-flaglow rc=$?"; env -u CLAUDE_CODE_EFFORT_LEVEL claude -p --model haiku --effort low --tools "" --strict-mcp-config --settings '{"disableAllHooks":true}' "Reply with the single word ok. probe=noenv-flaglow" > noenv-flaglow.out 2>&1; echo "noenv-flaglow rc=$?"; for f in /Users/roberthyatt/.claude/projects/-private-tmp-claude-502-taf-a-effort-probe/*.jsonl; do echo "$(rg -o -m1 'probe=[a-z-]+' "$f") efforts=$(rg -o '"effort":"[a-z]+"' "$f" | sort -u | tr '\n' ' ')"; done
```

Expected (measured at plan time in a sibling directory): both `rc=0`; `probe=envhigh-flaglow efforts="effort":"high"` and `probe=noenv-flaglow efforts="effort":"low"`. The inherited variable beats the flag. Record both lines in the findings. If `envhigh-flaglow` shows `low`, the CLI changed its precedence: record it, since the strip is still harmless.

**Step 5: Graders cite the rubric, single and batch**

```bash
for c in claude codex; do for m in single batch; do /Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/grader_rubric.py "$c" "$m" > "/private/tmp/claude-502/taf/a-rubric-$c-$m.log" 2>&1; echo "$c $m probe=$?"; cat "/private/tmp/claude-502/taf/a-rubric-$c-$m.log"; echo "$c $m rubric_lines=$(rg -c '^parsed=.*(Three Laws|Law [0-9]|[Cc]hecklist|F-Grade)' "/private/tmp/claude-502/taf/a-rubric-$c-$m.log")"; done; done
```

Expected:
- Each run shows `probe=0`. The Claude `child=` line shows `restricted=True`. The Codex `child=` model equals the opus `hook_newest=` that Step 6 prints (R7 newest Sol; `gpt-6.1-sol` in the plan-time cache).
- Each `parsed=` holds real verdicts with no `infrastructure_error`: one for single, two for batch.
- Each `rubric_lines=1`. If any run has an empty `rubric_lines=` or an `infrastructure_error`, STOP and report.

**Step 6: Codex model rule after**

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/taf/models.py > /private/tmp/claude-502/taf/a-models.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/taf/a-models.log
```

Expected:
- Four tier lines, each with `model_for=` equal to `hook_newest=` (local resolver) and `remote=` equal to `configured=` (remote keeps the configured slug).
- The `brain` line's `resolved=` equals the `hook_newest=` of `brain_model`'s tier when `brain_model` is a tier name (haiku/sonnet/opus/fable) or a `gpt-<v>-<word>` slug. It equals `brain_model` unchanged otherwise.

**Step 7: Full Commander suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/taf/suite.log 2>&1; echo "suite=$?"; rg -n '^(FAILED|ERROR)' /private/tmp/claude-502/taf/suite.log; tail -n 1 /private/tmp/claude-502/taf/suite.log
```

Expected: `suite=0` with 0 failed and no FAILED or ERROR lines.

**Step 8: Hooks suite**

```bash
for t in /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-*.sh; do bash "$t" > "/private/tmp/claude-502/taf/hooks-$(basename "$t").log" 2>&1; echo "$(basename "$t") rc=$?"; done; rg -l '^FAIL' /private/tmp/claude-502/taf/ --glob 'hooks-*.log'; echo "fail_files_rc=$?"
```

Expected: 42 lines, each `rc=0`; no file names printed; `fail_files_rc=1` (no matches).

**Step 9: State-manager suite**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test > /private/tmp/claude-502/taf/a-npm.log 2>&1; echo "npm=$?"; rg -n -e 'Test Files' -e 'Tests  ' -e ' FAIL ' /private/tmp/claude-502/taf/a-npm.log
```

Expected: `npm=0`, or FAIL lines exactly equal to the Task 1 Step 8 baseline failing set. Apply the Task 3 load-sensitive-test rule if the only extra failure is the SIGTERM-resistant process-group test.

**Step 10: Findings and staging**

Append an "After change" section to the findings. It holds the outputs of Steps 1-6 and 4b, any `/private/tmp/claude-502/taf/*.loaded` logs with their reruns (Task 3 load rule), every count next to its baseline value, and the summary lines of Steps 7-9. Then:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-17-tool-audit-fixes-design.md docs/plans/2026-10-17-tool-audit-fixes.md docs/plans/2026-10-17-tool-audit-fixes.plan.json docs/plans/2026-10-17-tool-audit-fixes-findings.md; git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status
```

Expected:
- `M` for these 20 files from Tasks 2-7:
  - Commander source: `fast_model.py`, `orchestrator_mcp.py`, `provider_config.py`, `provider_router.py`, `codex_brain_client.py`.
  - Commander tests: `conftest.py`, `test_fast_model.py`, `test_grader_routing.py`, `test_orchestrator_mcp.py`, `test_provider_config.py`, `test_provider_router.py`, `test_codex_brain_client.py`, `test_advisor_fallback_directive.py`.
  - Hooks: `plan-validator.sh`, `test-fast-model-fallback.sh`.
  - State-manager: `advisor-review.ts`, `advisor-review.test.ts`, `dist/index.js`.
  - Other: `SKILL.md`, `llm-backend.md`.
- `A` for the 4 tool-audit-fixes plan docs.
- Any other entries are only the 4 `docs/plans/2026-10-17-tool-audit*` docs (`-design`, `-findings`, `.md`, `.plan.json`) that were already staged before this plan began.
- `worker/.codex-plugin/plugin.json` does not appear.
