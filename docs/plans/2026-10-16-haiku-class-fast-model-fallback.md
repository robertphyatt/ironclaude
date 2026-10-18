# Haiku-Class Fast-Model Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Every fast-model consumer (the hook validator and the Commander's local-grader spots) tries the configured local box first, then a Haiku-class model (Claude Haiku, or Codex Luna), and fails open only when both fail. The Haiku-class children leave no session behind and run without IronClaude hooks or MCP servers.

**Requirements:** `docs/plans/2026-10-16-haiku-class-fast-model-fallback-design.md` (R1–R8 and the Fable adjustments).

**Architecture:**
- **Hooks.** `call_validation_llm` in `worker/hooks/plan-validator.sh` runs the local arm (`ollama` or `openai`) unless the backend is `haiku`. If the local result is empty, failed, or not JSON, it calls a new `_call_fast_model` step. Under Codex (`ic_is_codex`) that step runs `codex exec` with the newest installed `gpt-*-luna`; under Claude it runs `claude --model haiku` with `--no-session-persistence`, `--strict-mcp-config` and `--settings '{"disableAllHooks":true}'`. The step parses the answer into the plain JSON object the callers already read, under its own cap `fast_model_timeout_seconds`.
- **Commander.** A new module `commander/src/ironclaude/fast_model.py` holds the argv builders, the parsers (which return the FULL structured object) and `call_fast_model`. `LocalGrader.grade` calls it whenever the backend is `haiku` or the local result is any `infrastructure_error` (including an open circuit breaker). Because every local-grader spot goes through `LocalGrader.grade`, this one change covers `_call_local_grader` and its six call sites, the Brain-message validator, the awaiting-operator classifier, prompt detection and the Brain's permission-seeking check. `_grade_bounded` extends its join bound only when the fast model is answering. Session summarization gains a Haiku-class branch. The shadow grader skips quietly when the box is unreachable or the backend is `haiku`. The resolver default flips from `ollama` to `haiku`.
- **Episodic summarizer.** Fresh SDK calls run the system `claude` with `--no-session-persistence`, passed through the SDK's `extraArgs` and `pathToClaudeCodeExecutable`. The operator chose this because the installed SDK 0.1.42 has no `persistSession`. A live proof checks that no session file is written.

**Tech Stack:** bash hooks plus bash tests with stubs on PATH; Python 3 Commander plus pytest; TypeScript plus vitest (episodic-memory).

Loop: full. This touches hook enforcement code and the Commander grader routing.

## Execution invariants

- Use literal absolute paths everywhere. Shell state does not persist between commands, so every command `cd`s or uses absolute paths itself.
- Use `git -C /Users/roberthyatt/Code/ironclaude` for every git command.
- `docs/` is gitignored, so stage plan and findings files with `git add -f`.
- Run pytest with `PYTHONUNBUFFERED=1` and `commander/.venv/bin/python`, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Never stash, commit or push.
- Execution subagents must not use the Skill tool.
- Never edit `~/.claude/ironclaude-hooks-config.json`. It is a human-only guardrail file; even benign keys (such as `fast_model_timeout_seconds`) are changed only by the operator.
- Never use `2>/dev/null` on an evidence command. Each run writes its own log file under `/private/tmp/claude-502/` and prints its exit code.
- Placeholders `<FAST_MODEL_TIMEOUT_DEFAULT>`, `<FAST_LANE_FAST_MODEL_SECONDS>`, `<CODEX_SCHEMA_STRICT>` and `<NEWEST_LUNA>` are replaced by the literal values recorded in Task 1's findings "Gate values" block. Never invent them.
- If `claude` refuses to start inside the executor's own Claude Code session, run it with `env -u CLAUDECODE` (see memory `project_restart_commander_strip_claudecode`). This is a contingency, not a step.

**Verified at planning time** (read from source on 2026-10-16):

| What | Where / fact |
|---|---|
| Hook validator | `worker/hooks/plan-validator.sh` `call_validation_llm` `:97-229`. The haiku arm `"haiku"\|*)` is `:211-227` and echoes raw `claude --output-format json` output via `bash -c 'cd /tmp && echo "$1" \| claude …'` under `portable_timeout "$timeout_sec"`, where `timeout_sec` is `.timeout_seconds // 60` (`:114`). The ollama arm declares `local result=""` (`:156`) and ends with `export VALIDATION_LLM_RESPONSE="$result"` / `echo "$result"` (`:170-171`). The openai arm declares `local result` (`:197`) and ends the same way (`:207-208`). Neither local arm returns non-zero. |
| Hook helpers | `worker/hooks/hook-logger.sh`: `portable_timeout` `:64-83` uses `timeout`, then `gtimeout`, then a pure-bash fallback that kills only the backgrounded `"$@"` PID (`:73-75`). `ic_is_codex` `:146-155` checks `IC_HOOK_CLIENT`, then `PLUGIN_ROOT`/`CLAUDE_PLUGIN_ROOT` containing `/.codex`. |
| Callers | `get-back-to-work-impl.sh` `run_check` `:824-871` calls `call_validation_llm` inside `$(…)` (`:844`), treats a failure or empty output as "unavailable" (`:845-853`) and parses `jq -r ".$field_name"` (`:858`). Six checks run in parallel (`:942-972`). `task-completion-validator.sh:97-108` does the same with `\|\| { … exit 0; }`. Because both callers use `$(…)`, `VALIDATION_LLM_BACKEND` set inside the function is visible only to a direct (non-subshell) caller. That is pre-existing. |
| Hook test conventions | `worker/hooks/tests/test-openai-backend.sh` puts a recording `curl` stub on PATH (`:29-59`), uses `assert_eq`/`assert_contains` (`:11-21`), sources only `plan-validator.sh` (`:9`), and ends with `[ "$FAIL" -eq 0 ] && exit 0 \|\| exit 1` (`:337`). |
| Hook suites that reach the fallback | `test-gbtw-continuation-judge.sh` stubs only `curl` in `$ROOT/bin` (`:47-75`), puts it first on PATH (`:135`, `:221`) and expects "evaluation unavailable" for `MOCK_FAIL=1` (`:176`, `:184`, `:212`) and for a non-JSON answer (`:190`). `test-last-assistant-text.sh` case 5 (`:52-75`) points Ollama at `http://127.0.0.1:9` and expects "evaluation unavailable"; its env invocation is `:64-65` and does not touch PATH. After the chain, both would otherwise run the real `claude`/`codex`. |
| `make test-hooks` | `Makefile:40-44` loops over `worker/hooks/test-*.sh worker/hooks/tests/test-*.sh commander/hooks/tests/test-*.sh`, so a new `worker/hooks/tests/test-fast-model-fallback.sh` is picked up by the glob. No Makefile change is needed. |
| Timeout binaries on this host | `/opt/homebrew/bin/timeout` and `/opt/homebrew/bin/gtimeout` exist. `/usr/bin` and `/bin` contain neither. `/usr/bin/jq`, `/usr/bin/mktemp`, `/usr/bin/python3`, `/bin/sleep` and `/bin/bash` exist. So `PATH=<stubs>:/usr/bin:/bin` exercises the pure-bash fallback branch. |
| Commander resolver | `commander/src/ironclaude/backend_resolver.py`: `_COMMANDER_DEFAULT_BACKEND = "ollama"` (`:17`); `resolve_backend` `:43-93` treats every non-`openai` backend as Ollama (block `:63`, url default `:72`). No case in `worker/config-schema/resolution-cases.json` uses `haiku`. `commander/tests/test_backend_resolver.py:30-32` pins `ollama`. |
| LocalGrader | `commander/src/ironclaude/grader.py` `LocalGrader` `:48-288`: `__init__(config_path=None, timeout=None, keep_alive=None)` `:56`; `_build_client` `:103-123` (Ollama for any non-`openai` backend); `_get_client` `:125-143`; `_get_fast_client_state` `:145-168`; `grade(system_prompt, user_prompt, schema=None, *, read_timeout=None)` `:170-288` returns `{"infrastructure_error": True, "error_detail": …}` on every failure; a communication-profile error returns before any client call (`:191-196`). |
| Circuit breaker | `ollama_client.py`: process-global `_BREAKERS` (`:145`) opens after `_BREAKER_FAILURE_THRESHOLD = 3` failures (`:41`) for a 5.0 s base backoff (`:39`). When open, `_attempt` makes no request and raises `OllamaConnectionError("Ollama circuit open for all endpoints: …")` (`:248-249`), which `LocalGrader.grade` turns into an `infrastructure_error` (`:212-213`, `:251-254`). |
| Local-grader call sites | `orchestrator_mcp.py`: `LocalGrader` built at `:521` and `:523-527` (after `self._config = config or {}` at `:498`); `_call_local_grader` `:1335-1343` → `grader.grade(...)`, called at `:4841`, `:5706`, `:6239`, `:6322`, `:6377`, `:7447`. `main.py`: `self.config = config` `:1550`; `self._grader = LocalGrader(keep_alive="30m")` `:1620`; `_validate_brain_message` `:2082-2101` (`grade` at `:2088`); `_grade_bounded` `:3245-3294` (`grade(..., read_timeout=timeout)` at `:3269`, `t.join(timeout)` at `:3284`); `_detect_worker_prompt` `:4227-4262` (synchronous `grade` at `:4238`), called at `:4266`, `:4420`, `:5027`, `:5091`. `brain_client.py`: `LocalGrader(keep_alive="30m")` `:304`, `grade` at `:665`. `BrainClient.__init__` (`:234-242`) receives no Commander config. |
| Grader argv and parsers | `orchestrator_mcp.py` `_claude_grader_argv` `:1023-1049` has `--strict-mcp-config` but no `--no-session-persistence`; `_parse_claude_grader_output` `:1051-1089` handles array and single-object output; `_codex_grader_argv` `:1100-1116`; `_parse_codex_grader_output` `:1118-1144` takes the last `item.completed` `agent_message`; `_GRADER_ENV_STRIP` `:979-985`. `commander/tests/test_grader_routing.py:37-47` checks the claude argv by membership, not by exact list. |
| Session summarization | `orchestrator_mcp.py` `list_claude_sessions` `:6507-6616`: it resolves the `summarization` spot (`:6530`), takes the openai branch only for `openai` (`:6532-6543`) and an Ollama `get_ps()` branch for everything else (`:6544-6552`); per-session logic is `:6573-6607`. |
| Shadow grader | `shadow_grader.py` `_get_client` `:145-176` builds an Ollama client for any non-`openai` backend; `grade_with_tools` catches `OllamaError` at `:336-337` and `:408-409`. `orchestrator_mcp.py` `_run_shadow_and_report` `:1479-1526` posts to Slack (`:1500-1503`) and writes a concordance row. |
| Luna models | `provider_config.py:24` and `config.py:95` map the Codex `haiku` tier to `gpt-5.6-luna`; the default `grader` role is `{"preferred": "claude", "clients": ["claude"]}` (`config.py:105`). The broker resolves the newest installed Luna in `worker/mcp-servers/state-manager/src/tools/advisor-review.ts` (`readInstalledCodexModels` `:212-228`, `newestAtTier` `:201-209`, fallback `gpt-6-luna` at `:22`) and runs Codex with `exec --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only -m <model> -` (`:612-624`). `~/.codex/models_cache.json` lists `gpt-6-luna` and `gpt-5.6-luna`. |
| Test isolation | `commander/tests/conftest.py`: `_isolate_ic_env` (`:203-220`) unsets `IC_OLLAMA_CONFIG_PATH`; `_redirect_home` (`:254-274`) points `HOME` at an empty fake home, so in tests the hooks config is absent and resolves to the Commander default backend; `_reset_ollama_breakers` (`:223-230`). |
| Episodic summarizer | `worker/mcp-servers/episodic-memory/src/summarizer.ts` `callClaude` `:261-319` calls the Agent SDK `query` at `:288-301`; resume caller `:372`; fresh callers `:399`, `:430`. Installed SDK `node_modules/@anthropic-ai/claude-agent-sdk` is version `0.1.42`: `sdk.d.ts` has **no** `persistSession`, has `extraArgs` (`:475`) and `pathToClaudeCodeExecutable` (`:489`); the vendored `cli.js` contains `no-session-persistence` **0** times. `dist/` is gitignored (`.gitignore:29`, `worker/mcp-servers/*/dist/`) and untracked; `cli/mcp-server-wrapper.js` rebuilds `dist/` only when files are missing (`:63`, `:83`). Build script: `"build": "tsc && npm run bundle"`. |
| Session-file baseline | `/Users/roberthyatt/.claude/projects/-private-tmp/b97b76c6-da47-4e26-9c4d-941d3a49f633.jsonl` contains `hookName` 3 times (the positive control). |
| README | `README.md:247-258` is the "Validation Backend (Recommended)" section, which recommends local Ollama. |

---

## Task 1: Measure the lean Haiku/Luna children and compute the gate values

No tests required: measurement. No repository code changes.

**Files:**
- Create: `docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md`
- Stage only: `docs/plans/2026-10-16-haiku-class-fast-model-fallback-design.md`, `docs/plans/2026-10-16-haiku-class-fast-model-fallback.md`, `docs/plans/2026-10-16-haiku-class-fast-model-fallback.plan.json`

Scratch directory: `/private/tmp/claude-502/haiku-measure/` (outside the repo).

### Step 1: Prepare the scratch inputs

Create the scratch directory and three files in it:
- `schema.json`, the hooks' real `EVIDENCE_SCHEMA` (`get-back-to-work-impl.sh:812`) verbatim: `{"type":"object","properties":{"reasoning":{"type":"string"},"evidence_quality":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","evidence_quality"]}`
- `prompt.txt`: `Grade whether the assistant backed its claims with evidence. TRANSCRIPT: User: what does f() return? Assistant: I read f.py; line 2 returns 3, and the test test_f passed. Respond with ONLY JSON: {"reasoning": "...", "evidence_quality": "A/B/C/D/F"}`
- `measure.py`:

```python
#!/usr/bin/env python3
"""measure.py RUNS CONCURRENCY STDIN_FILE OUT_PREFIX CWD -- ARGV...
Runs ARGV RUNS times, CONCURRENCY at a time, stdin from STDIN_FILE, in CWD.
Writes OUT_PREFIX-<i>.out/.err and prints per-run rc/seconds plus nearest-rank p95."""
import json, math, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

runs, conc, stdin_path, prefix, cwd = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
argv = sys.argv[sys.argv.index("--") + 1:]

def one(i):
    with open(stdin_path, "rb") as f:
        t0 = time.monotonic()
        p = subprocess.run(argv, stdin=f, cwd=cwd, capture_output=True)
        dt = time.monotonic() - t0
    with open(f"{prefix}-{i}.out", "wb") as o:
        o.write(p.stdout)
    with open(f"{prefix}-{i}.err", "wb") as e:
        e.write(p.stderr)
    return {"run": i, "rc": p.returncode, "seconds": round(dt, 2)}

with ThreadPoolExecutor(max_workers=conc) as ex:
    results = list(ex.map(one, range(1, runs + 1)))
secs = sorted(r["seconds"] for r in results)
print(json.dumps({"results": results, "min": secs[0], "max": secs[-1],
                  "p95_nearest_rank": secs[math.ceil(0.95 * len(secs)) - 1]}))
```

Write all three files through Bash. The professional-mode guard blocks the Write/Edit tools on absolute paths outside the repo that are not in `allowed_files`.
```bash
mkdir -p /private/tmp/claude-502/haiku-measure && cd /private/tmp/claude-502/haiku-measure && printf '%s' '{"type":"object","properties":{"reasoning":{"type":"string"},"evidence_quality":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","evidence_quality"]}' > schema.json && printf '%s' 'Grade whether the assistant backed its claims with evidence. TRANSCRIPT: User: what does f() return? Assistant: I read f.py; line 2 returns 3, and the test test_f passed. Respond with ONLY JSON: {"reasoning": "...", "evidence_quality": "A/B/C/D/F"}' > prompt.txt && cat > measure.py <<'PYEOF'
<measure.py exactly as shown above>
PYEOF
ls -la /private/tmp/claude-502/haiku-measure
```
Expected: `schema.json`, `prompt.txt` and `measure.py` are listed, each with a non-zero size.

### Step 2: Single-call Claude latency (5 sequential runs)

```bash
cd /private/tmp/claude-502/haiku-measure && python3 measure.py 5 1 prompt.txt single /tmp -- claude --model haiku --print --tools "" --no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true}' --output-format json --json-schema "$(cat schema.json)" > single.json 2> single.log; echo "single=$?"; cat single.json
```
Expected: `single=0`, and every `results[].rc` is 0. If any rc is non-zero, show that run's `.err` file and STOP.

### Step 3: Six concurrent Claude children (the Stop hook's parallel checks)

```bash
cd /private/tmp/claude-502/haiku-measure && python3 measure.py 6 6 prompt.txt conc /tmp -- claude --model haiku --print --tools "" --no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true}' --output-format json --json-schema "$(cat schema.json)" > conc.json 2> conc.log; echo "conc=$?"; cat conc.json
```
Expected: `conc=0`, and all six rc are 0.

### Step 4: Output shape, resolved model, and no persisted session

```bash
cd /private/tmp/claude-502/haiku-measure && for f in single-*.out conc-*.out; do printf '%s ' "$f"; jq -c '(if type=="array" then .[] else . end) | select(.type=="result") | {so: .structured_output, models: ((.modelUsage // {}) | keys), sid: .session_id}' "$f"; done > shape.log 2>&1; echo "shape=$?"; cat shape.log; for sid in $(jq -r '(if type=="array" then .[] else . end) | select(.type=="result") | .session_id' single-*.out conc-*.out); do if [ -e "/Users/roberthyatt/.claude/projects/-private-tmp/$sid.jsonl" ]; then echo "PERSISTED $sid"; else echo "absent $sid"; fi; done > persist.log 2>&1; cat persist.log
```
Expected:
- `shape=0`. Every line has `so` as an object with `evidence_quality` in A–F, and `models` lists a model name containing `haiku`. Record that model name.
- `persist.log` holds 11 lines, all beginning `absent`. Session ids are checked one by one because other live sessions also write into `-private-tmp`.

### Step 5: Prove hooks are off in the child (one persisted run)

Run once **without** `--no-session-persistence` (still with `--settings '{"disableAllHooks":true}'`) so a transcript exists to inspect. Grep it for `hookName` and compare with the known-hooked control.

```bash
cd /private/tmp/claude-502/haiku-measure && python3 measure.py 1 1 prompt.txt hooksoff /tmp -- claude --model haiku --print --tools "" --strict-mcp-config --settings '{"disableAllHooks":true}' --output-format json --json-schema "$(cat schema.json)" > hooksoff.json 2> hooksoff.log; echo "hooksoff=$?"; sid=$(jq -r '(if type=="array" then .[] else . end) | select(.type=="result") | .session_id' hooksoff-1.out); echo "sid=$sid"; ls -la "/Users/roberthyatt/.claude/projects/-private-tmp/$sid.jsonl"; grep -c hookName "/Users/roberthyatt/.claude/projects/-private-tmp/$sid.jsonl"; echo "child_grep=$?"; grep -c hookName /Users/roberthyatt/.claude/projects/-private-tmp/b97b76c6-da47-4e26-9c4d-941d3a49f633.jsonl
```
Expected:
- `hooksoff=0`, and the transcript file exists.
- The child grep prints `0` and `child_grep=1` (no match).
- The control grep prints `3`.

If the child transcript contains `hookName`, STOP and report to the operator: the hooks-off mechanism is not proven. Do not switch to the `IC_HOOK_CHILD` alternative without the operator.

### Step 6: Codex Luna latency, shape, and no rollout

```bash
cd /private/tmp/claude-502/haiku-measure && LUNA=$(jq -r '[.models[]? | .slug? | select(type=="string" and test("^gpt-[0-9]+(\\.[0-9]+)*-luna$"))] | sort_by(capture("^gpt-(?<v>[0-9.]+)-luna$").v | split(".") | map(tonumber)) | last // "gpt-6-luna"' /Users/roberthyatt/.codex/models_cache.json); echo "luna=$LUNA"; touch codex-marker; python3 measure.py 3 1 prompt.txt codex /tmp -- codex exec --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only -c 'model_reasoning_effort="low"' -m "$LUNA" --output-schema /private/tmp/claude-502/haiku-measure/schema.json - > codex.json 2> codex.log; echo "codex=$?"; cat codex.json; for f in codex-*.out; do printf '%s ' "$f"; jq -Rc 'fromjson? | select(.type=="item.completed" and .item.type=="agent_message") | .item.text' "$f" | tail -n 1; jq -Rr 'fromjson? | select(.type=="thread.started") | .thread_id' "$f"; done > codex-shape.log 2>&1; cat codex-shape.log; find /Users/roberthyatt/.codex/sessions -newer codex-marker -type f > codex-rollouts.log 2>&1; echo "rollouts=$(wc -l < codex-rollouts.log)"
```
Expected:
- `luna=gpt-6-luna`, the newest installed today. Record it as `NEWEST_LUNA`.
- `codex=0`, with all three rc 0.
- Each `codex-*.out` has a last `agent_message` whose text parses as an object with `evidence_quality`, plus a `thread_id`.
- `rollouts=0`, unless another live Codex session wrote one. In that case, check each listed file name against the three `thread_id`s; none may match.

**Schema strictness.** `schema.json` deliberately has no `additionalProperties`. If any run's `.err` shows a schema rejection (for example "additionalProperties"), set `CODEX_SCHEMA_STRICT=yes`, add `"additionalProperties": false` to a copy of the schema, and repeat this step with the copy. Otherwise set `CODEX_SCHEMA_STRICT=no`.

### Step 7: Compute the gate values and write the findings doc

Write `docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md`. It records:
- the raw `single.json`, `conc.json` and `codex.json` numbers;
- the resolved Claude model name;
- `persist.log`, the hooks-off greps, and the Codex shape and rollout results;
- the Claude CLI version (`claude --version`) and Codex version (`codex --version`).

End it with this block, filled mechanically:

```
## Gate values
P95_SINGLE_CLAUDE=<single.json p95_nearest_rank>
P95_CONCURRENT_CLAUDE=<conc.json p95_nearest_rank>
P95_SINGLE_CODEX=<codex.json p95_nearest_rank>
FAST_MODEL_TIMEOUT_DEFAULT=max(30, ceil(2 × P95_CONCURRENT_CLAUDE)) = <integer>
FAST_LANE_FAST_MODEL_SECONDS=ceil(2 × P95_SINGLE_CLAUDE) = <integer>
PROMPT_DETECTION_SYNC=<yes if P95_SINGLE_CLAUDE ≤ 5, else STOP>
HOOKS_OFF_PROVEN=<yes if Step 5 child grep is 0 and control is 3, else STOP>
NO_PERSISTENCE_PROVEN=<yes if persist.log is all "absent", else STOP>
CODEX_NO_ROLLOUT=<yes if no rollout matches a measured thread_id, else STOP>
CODEX_WITHIN_CAP=<yes if P95_SINGLE_CODEX < FAST_MODEL_TIMEOUT_DEFAULT, else STOP>
CODEX_SCHEMA_STRICT=<yes|no>
NEWEST_LUNA=<slug>
```

STOP rule: if any value reads STOP, stop execution and report the findings to the operator. Do not improvise an async redesign of prompt detection, a different hooks-off mechanism, or a different cap.
- If `PROMPT_DETECTION_SYNC=STOP`, report `P95_SINGLE_CLAUDE` and ask the operator to choose: accept synchronous detection at that latency, or start a follow-up design that moves detection off the poll thread (design R6).
- Note for the operator: the 5 s threshold is per call. With K workers emitting new output in one poll, the poll thread spends up to K × P95, because the cache is keyed per log-tail hash.

### Step 8: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md docs/plans/2026-10-16-haiku-class-fast-model-fallback-design.md docs/plans/2026-10-16-haiku-class-fast-model-fallback.md docs/plans/2026-10-16-haiku-class-fast-model-fallback.plan.json
```
Expected: no output.

---

## Task 2: Hooks — fast-model step and chain in `call_validation_llm`

TDD.

**Files:**
- Create: `worker/hooks/tests/test-fast-model-fallback.sh`
- Modify: `worker/hooks/plan-validator.sh` (`:15`, `:97-229`)
- Modify: `worker/hooks/tests/test-gbtw-continuation-judge.sh` (after `:75`)
- Modify: `worker/hooks/tests/test-last-assistant-text.sh` (`:56-65`)

### Step 1: Write the test (RED)

Create `worker/hooks/tests/test-fast-model-fallback.sh` with this content, substituting `<FAST_MODEL_TIMEOUT_DEFAULT>` from the findings doc. If `CODEX_SCHEMA_STRICT=yes`, the codex-schema assertion expects `$SCHEMA` with `"additionalProperties":false` appended (`jq -c '. + {additionalProperties:false}'`).

```bash
#!/bin/bash
# test-fast-model-fallback.sh — the Haiku-class fast-model step and chain in
# plan-validator.sh call_validation_llm(): local box -> Claude Haiku (or the newest
# installed Codex Luna) -> fail open. Stub claude/codex/curl on PATH record argv.
# Do NOT set -e.
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

cat > "$STUBBIN/claude" <<'EOF'
#!/bin/bash
: > "$STUB_LOG_DIR/claude.argv"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_LOG_DIR/claude.argv"; done
pwd -P > "$STUB_LOG_DIR/claude.cwd"
cat > "$STUB_LOG_DIR/claude.stdin"
case "${STUB_CLAUDE_MODE:-array}" in
  array)  printf '%s' '[{"type":"system","subtype":"init"},{"type":"assistant"},{"type":"result","subtype":"success","structured_output":{"ok":true,"reason":"from-array"}}]' ;;
  object) printf '%s' '{"type":"result","subtype":"success","structured_output":{"ok":true,"reason":"from-object"}}' ;;
  noso)   printf '%s' '[{"type":"result","subtype":"error_max_turns","is_error":true}]' ;;
  fail)   exit 1 ;;
  hang)   echo "$$" > "$STUB_LOG_DIR/claude.pid"; exec /bin/sleep 30 ;;
esac
EOF

cat > "$STUBBIN/codex" <<'EOF'
#!/bin/bash
: > "$STUB_LOG_DIR/codex.argv"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_LOG_DIR/codex.argv"; done
prev=""; for a in "$@"; do [ "$prev" = "--output-schema" ] && cp "$a" "$STUB_LOG_DIR/codex.schema"; prev="$a"; done
cat > "$STUB_LOG_DIR/codex.stdin"
case "${STUB_CODEX_MODE:-ok}" in
  ok)
    printf '%s\n' '{"type":"thread.started","thread_id":"t-1"}'
    printf '%s\n' 'not a json line'
    printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"{\"ok\":false,\"reason\":\"early\"}"}}'
    printf '%s\n' '{"type":"item.completed","item":{"type":"agent_message","text":"{\"ok\":true,\"reason\":\"last\"}"}}'
    printf '%s\n' '{"type":"turn.completed"}' ;;
  fail) exit 1 ;;
esac
EOF

cat > "$STUBBIN/curl" <<'EOF'
#!/bin/bash
echo called >> "$STUB_LOG_DIR/curl.calls"
case "${STUB_CURL_MODE:-fail}" in
  fail)    exit 7 ;;
  nonjson) printf '%s' '{"choices":[{"message":{"content":"not json"}}]}' ;;
  ok)      printf '%s' '{"choices":[{"message":{"content":"{\"ok\":true,\"reason\":\"local\"}"}}]}' ;;
esac
EOF
chmod +x "$STUBBIN/claude" "$STUBBIN/codex" "$STUBBIN/curl"
export PATH="$STUBBIN:$PATH"

SCHEMA='{"type":"object","properties":{"ok":{"type":"boolean"},"reason":{"type":"string"}},"required":["ok","reason"]}'
reset_logs() { rm -f "$STUB_LOG_DIR"/*; }
cfg() { printf '%s' "$1" > "$WORK/cfg.json"; export IC_OLLAMA_CONFIG_PATH="$WORK/cfg.json"; }
# Direct call (not $(...)) so VALIDATION_LLM_BACKEND is visible here.
run_cvl() {
  reset_logs
  call_validation_llm "grade this" "$SCHEMA" > "$WORK/out"; RC=$?
  OUT=$(jq -c . "$WORK/out" 2>/dev/null); RAW=$(cat "$WORK/out"); BACKEND="$VALIDATION_LLM_BACKEND"
}
called() { [ -e "$STUB_LOG_DIR/$1" ] && echo yes || echo no; }
export IC_HOOK_CLIENT=claude
unset STUB_CLAUDE_MODE STUB_CODEX_MODE STUB_CURL_MODE

echo "=== Claude event-array output is parsed to the plain object ==="
cfg '{"backend":"haiku"}'; STUB_CLAUDE_MODE=array; export STUB_CLAUDE_MODE
run_cvl
assert_eq "array -> structured_output object" '{"ok":true,"reason":"from-array"}' "$OUT"
assert_eq "array -> rc 0" "0" "$RC"
assert_eq "array -> backend label haiku" "haiku" "$BACKEND"
assert_eq "backend haiku never calls curl" "no" "$(called curl.calls)"
assert_eq "prompt delivered on stdin" "grade this" "$(cat "$STUB_LOG_DIR/claude.stdin")"
assert_eq "claude child cwd is /tmp" "/private/tmp" "$(cat "$STUB_LOG_DIR/claude.cwd")"
assert_eq "claude isolation flags (exact argv)" \
  "--model|haiku|--print|--tools||--no-session-persistence|--strict-mcp-config|--settings|{\"disableAllHooks\":true}|--output-format|json|--json-schema|$SCHEMA|" \
  "$(tr '\n' '|' < "$STUB_LOG_DIR/claude.argv")"

echo "=== Claude single result object is parsed ==="
STUB_CLAUDE_MODE=object; run_cvl
assert_eq "object -> structured_output object" '{"ok":true,"reason":"from-object"}' "$OUT"

echo "=== Claude result without structured_output fails open ==="
STUB_CLAUDE_MODE=noso; run_cvl
assert_eq "no structured_output -> rc 1" "1" "$RC"
assert_eq "no structured_output -> empty stdout" "" "$RAW"

echo "=== openai failure falls back to Haiku ==="
cfg '{"backend":"openai","openai":{"base_url":"http://h/v1","model":"m"}}'
STUB_CURL_MODE=fail; STUB_CLAUDE_MODE=array; export STUB_CURL_MODE
run_cvl
assert_eq "openai failed -> curl was tried" "yes" "$(called curl.calls)"
assert_eq "openai failed -> haiku answer" '{"ok":true,"reason":"from-array"}' "$OUT"
assert_eq "openai failed -> backend label haiku" "haiku" "$BACKEND"

echo "=== openai non-JSON answer falls back to Haiku ==="
STUB_CURL_MODE=nonjson; run_cvl
assert_eq "openai non-JSON -> haiku answer" '{"ok":true,"reason":"from-array"}' "$OUT"

echo "=== openai JSON answer is used; no fast-model call ==="
STUB_CURL_MODE=ok; run_cvl
assert_eq "openai ok -> local answer" '{"ok":true,"reason":"local"}' "$OUT"
assert_eq "openai ok -> claude not called" "no" "$(called claude.argv)"
assert_eq "openai ok -> backend label openai:m" "openai:m" "$BACKEND"

echo "=== both steps failing returns failure (fail open) ==="
STUB_CURL_MODE=fail; STUB_CLAUDE_MODE=fail; run_cvl
assert_eq "both fail -> rc 1" "1" "$RC"
assert_eq "both fail -> empty stdout" "" "$RAW"

echo "=== Codex client uses codex exec with the newest installed Luna ==="
export IC_HOOK_CLIENT=codex CODEX_HOME="$WORK/codexhome"; mkdir -p "$CODEX_HOME"
printf '%s' '{"models":[{"slug":"gpt-5.9-luna"},{"slug":"gpt-5.10-luna"},{"slug":"gpt-6-sol"},{"slug":null},{}]}' > "$CODEX_HOME/models_cache.json"
cfg '{"backend":"haiku"}'; STUB_CODEX_MODE=ok; export STUB_CODEX_MODE
run_cvl
assert_eq "codex -> last agent_message object" '{"ok":true,"reason":"last"}' "$OUT"
assert_eq "codex -> backend label luna:<numeric-newest>" "luna:gpt-5.10-luna" "$BACKEND"
assert_eq "codex -> claude not called" "no" "$(called claude.argv)"
assert_eq "codex isolation flags (argv head)" \
  'exec|--json|--ephemeral|--ignore-user-config|--ignore-rules|--skip-git-repo-check|-s|read-only|-c|model_reasoning_effort="low"|-m|gpt-5.10-luna|--output-schema|' \
  "$(head -n 13 "$STUB_LOG_DIR/codex.argv" | tr '\n' '|')"
assert_eq "codex argv ends with - (stdin prompt)" "-" "$(tail -n 1 "$STUB_LOG_DIR/codex.argv")"
assert_eq "codex schema file carries the caller schema" "$(printf '%s' "$SCHEMA" | jq -c .)" "$(jq -c . "$STUB_LOG_DIR/codex.schema")"
assert_eq "codex prompt delivered on stdin" "grade this" "$(cat "$STUB_LOG_DIR/codex.stdin")"

echo "=== Codex with no models cache falls back to gpt-6-luna ==="
rm -f "$CODEX_HOME/models_cache.json"; run_cvl
assert_eq "no cache -> luna:gpt-6-luna" "luna:gpt-6-luna" "$BACKEND"

echo "=== Codex failure fails open ==="
STUB_CODEX_MODE=fail; run_cvl
assert_eq "codex fail -> rc 1" "1" "$RC"
export IC_HOOK_CLIENT=claude; unset CODEX_HOME STUB_CODEX_MODE

echo "=== fast_model_timeout_seconds resolution ==="
assert_eq "unset -> measured default" "<FAST_MODEL_TIMEOUT_DEFAULT>" "$(_resolve_fast_model_timeout '{}')"
assert_eq "empty config -> measured default" "<FAST_MODEL_TIMEOUT_DEFAULT>" "$(_resolve_fast_model_timeout '')"
assert_eq "configured 7 -> 7" "7" "$(_resolve_fast_model_timeout '{"fast_model_timeout_seconds":7}')"
assert_eq "non-number -> measured default" "<FAST_MODEL_TIMEOUT_DEFAULT>" "$(_resolve_fast_model_timeout '{"fast_model_timeout_seconds":"x"}')"

echo "=== timeout cap is enforced and the child is killed (timeout binary branch) ==="
cfg '{"backend":"haiku","fast_model_timeout_seconds":2}'; STUB_CLAUDE_MODE=hang
start=$SECONDS; run_cvl; elapsed=$((SECONDS - start)); sleep 1
assert_eq "cap -> rc 1" "1" "$RC"
assert_eq "cap -> returned within 6s" "yes" "$([ "$elapsed" -le 6 ] && echo yes || echo no)"
assert_eq "cap -> child process killed" "dead" "$(kill -0 "$(cat "$STUB_LOG_DIR/claude.pid")" 2>/dev/null && echo alive || echo dead)"

echo "=== timeout cap is enforced and the child is killed (pure-bash fallback branch) ==="
FALLBACK_PATH="$STUBBIN:/usr/bin:/bin"
assert_eq "precondition: no timeout/gtimeout on reduced PATH" "absent" \
  "$(PATH="$FALLBACK_PATH" bash -c 'command -v timeout || command -v gtimeout' >/dev/null && echo present || echo absent)"
OLD_PATH="$PATH"; PATH="$FALLBACK_PATH"
start=$SECONDS; run_cvl; elapsed=$((SECONDS - start)); sleep 1
PATH="$OLD_PATH"
assert_eq "fallback cap -> rc 1" "1" "$RC"
assert_eq "fallback cap -> returned within 6s" "yes" "$([ "$elapsed" -le 6 ] && echo yes || echo no)"
assert_eq "fallback cap -> child process killed" "dead" "$(kill -0 "$(cat "$STUB_LOG_DIR/claude.pid")" 2>/dev/null && echo alive || echo dead)"

rm -rf "$WORK"
unset IC_OLLAMA_CONFIG_PATH IC_HOOK_CLIENT STUB_CLAUDE_MODE STUB_CURL_MODE
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
```

RED run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-fast-model-fallback.sh > /private/tmp/claude-502/hfm-t2-red.log 2>&1; echo "red=$?"; grep -c '^FAIL' /private/tmp/claude-502/hfm-t2-red.log; tail -n 1 /private/tmp/claude-502/hfm-t2-red.log
```
Expected (derived from current code):
- `red=1`, and the last line reads `Results: N passed, M failed` with M ≥ 1.
- The log contains `FAIL: array -> structured_output object`, because the current haiku arm echoes the raw event array.
- It contains `FAIL: openai failed -> haiku answer`, because the openai arm echoes empty output and returns 0.
- It contains `FAIL: codex -> backend label luna:<numeric-newest>`.
- It contains a `_resolve_fast_model_timeout: command not found` failure.

The hang cases may take about 30 s at RED, because the old arm uses `timeout_seconds` (60). Record the measured output.

### Step 2: Implementation (GREEN) in `worker/hooks/plan-validator.sh`

1. Replace the header line `:15` (`#   - call_validation_llm() — centralized LLM call (Ollama or Haiku)`) with:
```
#   - call_validation_llm() — centralized LLM call: local box (Ollama/OpenAI-compatible),
#     then the Haiku-class fast model (Claude Haiku, or the newest Codex Luna), then fail open
```
2. Insert directly before the `# CENTRALIZED LLM VALIDATION` banner block (the `# ====` line at `:86`):
```bash
# =============================================================================
# HAIKU-CLASS FAST MODEL (chain step after the local box; see llm-backend.md)
# =============================================================================

# Default for fast_model_timeout_seconds, set from measured lean-child latency
# (docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md).
IC_FAST_MODEL_TIMEOUT_DEFAULT=<FAST_MODEL_TIMEOUT_DEFAULT>

# Pure helper: fast-model cap from config JSON content ($1); default when unset/invalid.
_resolve_fast_model_timeout() {
  local t
  t=$(printf '%s' "$1" | jq -r --argjson d "$IC_FAST_MODEL_TIMEOUT_DEFAULT" \
    '.fast_model_timeout_seconds // $d | if type == "number" and . > 0 then floor else $d end' 2>/dev/null) || t=""
  [[ "$t" =~ ^[0-9]+$ ]] || t="$IC_FAST_MODEL_TIMEOUT_DEFAULT"
  echo "$t"
}

# Newest installed gpt-<version>-luna from the Codex models cache, else gpt-6-luna.
# Mirrors readInstalledCodexModels/newestAtTier in state-manager advisor-review.ts.
_newest_luna() {
  local cache="${CODEX_HOME:-$HOME/.codex}/models_cache.json" slug=""
  if [ -r "$cache" ]; then
    slug=$(jq -r '[.models[]? | .slug? | select(type == "string" and test("^gpt-[0-9]+(\\.[0-9]+)*-luna$"))]
      | sort_by(capture("^gpt-(?<v>[0-9.]+)-luna$").v | split(".") | map(tonumber)) | last // empty' "$cache" 2>/dev/null) || slug=""
  fi
  echo "${slug:-gpt-6-luna}"
}

# claude --output-format json: an event ARRAY (answer in the type=="result" element's
# structured_output) or a single result object. Prints the compact object, or nothing.
_extract_claude_structured() {
  jq -c 'if type == "array" then (map(select(type == "object" and .type == "result")) | last | .structured_output?)
         elif type == "object" and (.type == "result" or has("structured_output")) then .structured_output
         else null end
         | select(type == "object")' 2>/dev/null
}

# codex exec --json JSONL: the LAST item.completed agent_message text, parsed as a JSON
# object. Non-JSON lines are skipped. Prints the compact object, or nothing.
_extract_codex_agent_json() {
  jq -Rc 'fromjson? | select(type == "object" and .type == "item.completed" and (.item | type) == "object" and .item.type == "agent_message") | .item.text' 2>/dev/null \
    | tail -n 1 | jq -r '. // empty' 2>/dev/null | jq -c 'select(type == "object")' 2>/dev/null
}

# Run the Haiku-class model once under its own cap.
# Args: $1 prompt, $2 JSON schema ('{"type":"object"}' when empty), $3 cap seconds, $4 luna slug (Codex only)
# Output: the plain JSON object. Returns 1 on timeout, non-zero exit or unparseable output.
# The child is exec'd from `bash -c`, so the PID that portable_timeout kills (in any
# branch, including the pure-bash fallback) IS the claude/codex process.
_call_fast_model() {
  local prompt="$1" schema="${2:-}" cap="$3" luna="$4" work out="" rc=0
  [ -n "$schema" ] && [ "$schema" != "{}" ] || schema='{"type":"object"}'
  work=$(mktemp -d "${TMPDIR:-/tmp}/ic-fast-model.XXXXXX") || return 1
  printf '%s' "$prompt" > "$work/prompt"
  if ic_is_codex; then
    printf '%s' "$schema" > "$work/schema.json"
    portable_timeout "$cap" bash -c 'cd /tmp && exec "$@" < "$0"' "$work/prompt" \
      codex exec --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check \
      -s read-only -c 'model_reasoning_effort="low"' -m "$luna" --output-schema "$work/schema.json" - \
      > "$work/out" 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] && out=$(_extract_codex_agent_json < "$work/out")
  else
    portable_timeout "$cap" bash -c 'cd /tmp && exec "$@" < "$0"' "$work/prompt" \
      claude --model haiku --print --tools "" --no-session-persistence --strict-mcp-config \
      --settings '{"disableAllHooks":true}' --output-format json --json-schema "$schema" \
      > "$work/out" 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] && out=$(_extract_claude_structured < "$work/out")
  fi
  rm -rf "$work"
  [ "$rc" -eq 0 ] && [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}

```
   If `CODEX_SCHEMA_STRICT=yes`, the Codex branch writes the schema with `printf '%s' "$schema" | jq -c '. + {additionalProperties: false}' > "$work/schema.json"` instead of `printf '%s' "$schema" > "$work/schema.json"`.
3. In `call_validation_llm`, add `local result=""` on the line after `local hook_budget=8`-block (directly before `case "$backend" in` at `:126`). Change the ollama arm's `local result=""` (`:156`) to `result=""` and delete the openai arm's `local result` (`:197`).
4. Delete the two lines `export VALIDATION_LLM_RESPONSE="$result"` and `echo "$result"` at the end of the ollama arm (`:170-171`) and of the openai arm (`:207-208`).
5. Replace the whole haiku arm (`:211-227`, from `"haiku"|*)` through its `;;`) and the closing `esac` (`:228`) with:
```bash
    *)
      # "haiku" (and any unknown backend): there is no local box; go straight to the fast model.
      result=""
      ;;
  esac

  # R1 chain. A usable local answer is valid JSON (jq -e .). Empty, failed or non-JSON
  # local output falls back to the Haiku-class model. jq -e also rejects a bare
  # null/false, which no caller schema allows, so those fall back too.
  if [ -n "$result" ] && printf '%s' "$result" | jq -e . >/dev/null 2>&1; then
    export VALIDATION_LLM_RESPONSE="$result"
    echo "$result"
    return 0
  fi

  local fast_cap luna="" fast_result
  fast_cap=$(_resolve_fast_model_timeout "$(cat "$config" 2>/dev/null)")
  if ic_is_codex; then
    luna=$(_newest_luna)
    export VALIDATION_LLM_BACKEND="luna:${luna}"
  else
    export VALIDATION_LLM_BACKEND="haiku"
  fi
  fast_result=$(_call_fast_model "$prompt" "$schema" "$fast_cap" "$luna") || return 1
  export VALIDATION_LLM_RESPONSE="$fast_result"
  echo "$fast_result"
```
   The function's closing `}` (`:229`) stays.

### Step 3: Keep the two suites that reach the fallback hermetic

1. In `worker/hooks/tests/test-gbtw-continuation-judge.sh`, insert after `chmod +x "$ROOT/bin/curl"` (`:75`):
```bash
# The validator chain falls back to the Haiku-class model when the local box fails or
# answers non-JSON. Fail those children so the unavailable paths stay hermetic.
printf '#!/bin/bash\nexit 1\n' > "$ROOT/bin/claude"
printf '#!/bin/bash\nexit 1\n' > "$ROOT/bin/codex"
chmod +x "$ROOT/bin/claude" "$ROOT/bin/codex"
```
2. In `worker/hooks/tests/test-last-assistant-text.sh`, insert after `mkdir -p "$E2E_HOME/.claude"` (`:57`):
```bash
# The validator chain falls back to the Haiku-class model; keep this case hermetic.
mkdir -p "$TMP_ROOT/bin"
printf '#!/bin/bash\nexit 1\n' > "$TMP_ROOT/bin/claude"
printf '#!/bin/bash\nexit 1\n' > "$TMP_ROOT/bin/codex"
chmod +x "$TMP_ROOT/bin/claude" "$TMP_ROOT/bin/codex"
```
   Then change `HOME="$E2E_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)` (`:65`) to `HOME="$E2E_HOME" PATH="$TMP_ROOT/bin:$PATH" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)`.

### Step 4: GREEN plus hook regression

Before the full suite, create a sentinel so no suite can spawn a real model. Every hook suite runs with sentinel `claude`/`codex` first on PATH; they log their calls and exit 1, which matches today's "unavailable" outcome.
```bash
mkdir -p /private/tmp/claude-502/hfm-sentinel/bin && printf '#!/bin/bash\necho "$(pwd) $(basename "$0") $*" >> /private/tmp/claude-502/hfm-sentinel/calls.log\nexit 1\n' > /private/tmp/claude-502/hfm-sentinel/bin/claude && cp /private/tmp/claude-502/hfm-sentinel/bin/claude /private/tmp/claude-502/hfm-sentinel/bin/codex && chmod +x /private/tmp/claude-502/hfm-sentinel/bin/claude /private/tmp/claude-502/hfm-sentinel/bin/codex; rm -f /private/tmp/claude-502/hfm-sentinel/calls.log; for t in test-fast-model-fallback test-openai-backend test-gbtw-continuation-judge test-last-assistant-text; do bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/$t.sh > /private/tmp/claude-502/hfm-t2-$t.log 2>&1; echo "$t=$?"; tail -n 1 /private/tmp/claude-502/hfm-t2-$t.log; done; PATH=/private/tmp/claude-502/hfm-sentinel/bin:$PATH make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/hfm-t2-hooks-all.log 2>&1; echo "hooks_all=$?"; tail -n 3 /private/tmp/claude-502/hfm-t2-hooks-all.log; ls -la /private/tmp/claude-502/hfm-sentinel/calls.log
```
Expected:
- `test-fast-model-fallback=0` with `Results: N passed, 0 failed`.
- `test-openai-backend=0`, `test-gbtw-continuation-judge=0` and `test-last-assistant-text=0`.
- `hooks_all=0`.
- `calls.log` may or may not exist. If it exists, record each line (it names suites that already called a real `claude`/`codex` before this change because they run the Stop hook with no config); do not modify those suites.

Any other failure must be shown pre-existing by running it in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 5: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/plan-validator.sh worker/hooks/tests/test-fast-model-fallback.sh worker/hooks/tests/test-gbtw-continuation-judge.sh worker/hooks/tests/test-last-assistant-text.sh
```
Expected: no output.

---

## Task 3: Commander fast-model helper module

TDD. The subprocess is stubbed only at the boundary, `fast_model._run_child`.

**Files:**
- Create: `commander/src/ironclaude/fast_model.py`
- Create: `commander/tests/test_fast_model.py`
- Modify: `commander/tests/conftest.py` (append one autouse fixture)

### Step 1: Test (RED)

Create `commander/tests/test_fast_model.py`:

```python
"""Unit tests for the Haiku-class fast-model helper (subprocess stubbed at _run_child)."""
import json
import os
import subprocess

import pytest

from ironclaude import fast_model

SCHEMA = {"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}


def _proc(stdout, rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


def _claude_array(obj):
    return json.dumps([{"type": "system"}, {"type": "result", "structured_output": obj}])


def _codex_jsonl(obj):
    return "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t"}),
        "not json",
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"kind": "early"})}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(obj)}}),
    ])


def _cfg(preferred="claude", clients=("claude",), codex_enabled=False):
    return {"providers": {
        "clients": {
            "claude": {"enabled": True, "path": "/bin/claude-x", "models": {"haiku": "haiku"}},
            "codex": {"enabled": codex_enabled, "path": "/bin/codex-x", "models": {"haiku": "gpt-5.6-luna"}},
        },
        "roles": {"grader": {"preferred": preferred, "clients": list(clients)}},
    }}


def test_claude_argv_is_lean_and_session_free():
    assert fast_model.claude_fast_argv("claude", "haiku", SCHEMA) == [
        "claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence",
        "--strict-mcp-config", "--settings", '{"disableAllHooks":true}',
        "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
    ]


def test_codex_argv_is_ephemeral_low_effort():
    assert fast_model.codex_fast_argv("codex", "gpt-6-luna", "/tmp/s.json") == [
        "codex", "exec", "-c", 'model_reasoning_effort="low"', "--json", "--ephemeral",
        "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
        "--output-schema", "/tmp/s.json", "-s", "read-only", "-m", "gpt-6-luna", "-",
    ]


def test_parse_claude_returns_full_object_from_array_or_object():
    full = {"kind": "menu", "question": "A or B?", "worker_id": "w1", "valid": True}
    assert fast_model.parse_claude_output(_claude_array(full)) == full
    assert fast_model.parse_claude_output(json.dumps({"type": "result", "structured_output": full})) == full


@pytest.mark.parametrize("stdout", [
    "not json", json.dumps([{"type": "result"}]), json.dumps([{"type": "result", "structured_output": [1]}]),
    json.dumps({"type": "assistant"}), "",
])
def test_parse_claude_rejects_unusable_output(stdout):
    assert fast_model.parse_claude_output(stdout) is None


def test_parse_codex_takes_last_agent_message_and_skips_noise():
    assert fast_model.parse_codex_output(_codex_jsonl({"kind": "none"})) == {"kind": "none"}
    assert fast_model.parse_codex_output("not json\n") is None
    assert fast_model.parse_codex_output(json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "prose"}})) is None


def test_clients_follow_grader_role_preference():
    assert fast_model.fast_model_clients(None) == [("claude", "claude", "haiku")]
    assert fast_model.fast_model_clients({"providers": "bad"}) == [("claude", "claude", "haiku")]
    assert fast_model.fast_model_clients(_cfg()) == [("claude", "/bin/claude-x", "haiku")]
    assert fast_model.fast_model_clients(
        _cfg(preferred="codex", clients=("codex", "claude"), codex_enabled=True)
    ) == [("codex", "/bin/codex-x", "gpt-5.6-luna"), ("claude", "/bin/claude-x", "haiku")]


@pytest.mark.parametrize("cfg,expected", [
    ({}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
    ({"fast_model_timeout_seconds": 7}, 7),
    ({"fast_model_timeout_seconds": "x"}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
    ({"fast_model_timeout_seconds": True}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
    ({"fast_model_timeout_seconds": 0}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
])
def test_fast_model_timeout(cfg, expected):
    assert fast_model.fast_model_timeout(cfg) == expected


def test_call_returns_full_object_with_lean_child(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-remove")
    seen = {}

    def fake(argv, *, input_text, timeout, env):
        seen.update(argv=argv, input=input_text, timeout=timeout, env=env)
        return _proc(_claude_array({"kind": "menu", "question": "Q?"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model("SYS", "USER", SCHEMA, timeout=11, commander_config=None)
    assert out == {"kind": "menu", "question": "Q?"}
    assert seen["argv"][:4] == ["claude", "-p", "--model", "haiku"]
    assert seen["input"] == "SYS\n\nUSER"
    assert seen["timeout"] == 11
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_timeout_moves_to_next_client_then_codex_answers(monkeypatch):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv[0])
        if argv[0] == "/bin/claude-x":
            raise subprocess.TimeoutExpired(argv, timeout)
        schema_file = argv[argv.index("--output-schema") + 1]
        assert json.load(open(schema_file)) == SCHEMA
        return _proc(_codex_jsonl({"kind": "none"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model(
        "S", "U", SCHEMA, timeout=5,
        commander_config=_cfg(preferred="claude", clients=("claude", "codex"), codex_enabled=True))
    assert out == {"kind": "none"}
    assert calls == ["/bin/claude-x", "/bin/codex-x"]


def test_codex_schema_file_is_removed(monkeypatch):
    seen = {}

    def fake(argv, *, input_text, timeout, env):
        seen["file"] = argv[argv.index("--output-schema") + 1]
        return _proc(_codex_jsonl({"kind": "none"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    fast_model.call_fast_model("S", "U", SCHEMA, timeout=5,
                               commander_config=_cfg(preferred="codex", clients=("codex",), codex_enabled=True))
    assert not os.path.exists(seen["file"])


def test_all_clients_failing_is_infrastructure_error(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc("", rc=1))
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None)
    assert out["infrastructure_error"] is True
    assert out["error_detail"].startswith("fast model unavailable:")
    assert "haiku exited 1" in out["error_detail"]


def test_missing_required_field_is_infrastructure_error(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_claude_array({"other": 1})))
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None)
    assert out["infrastructure_error"] is True
    assert "kind" in out["error_detail"]
```

If `CODEX_SCHEMA_STRICT=yes`, `test_timeout_moves_to_next_client_then_codex_answers` asserts `json.load(open(schema_file)) == {**SCHEMA, "additionalProperties": False}` instead.

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py -q > /private/tmp/claude-502/hfm-t3-red.log 2>&1; echo "red=$?"; tail -n 5 /private/tmp/claude-502/hfm-t3-red.log
```
Expected: `red` is non-zero (2, a collection error), and the log shows `ModuleNotFoundError: No module named 'ironclaude.fast_model'`.

### Step 2: Implementation (GREEN)

Create `commander/src/ironclaude/fast_model.py`, substituting the Task 1 gate values:

```python
"""Haiku-class fast-model step for the Commander's local-grader spots.

When the configured local box (ollama/openai) fails (any infrastructure_error,
including an open circuit breaker), answers non-JSON, or the backend is "haiku",
LocalGrader and session summarization call the Haiku-class tier: Claude Haiku, or
the codex client's configured Haiku-tier (Luna) model, in the `grader` role's client
order (preferred first). Children leave no session behind: claude runs with
--no-session-persistence, --strict-mcp-config and hooks disabled; codex with
--ephemeral --ignore-user-config --ignore-rules.

Deliberately NOT routed through OrchestratorTools._call_grader, which squashes
answers to {grade, approved, feedback}, holds _grader_lock and uses the grader tier.
Imports nothing from orchestrator_mcp (grader.py must not import it).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Measured 2026-10-16 (docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md):
# max(30, ceil(2 x p95 of six concurrent lean Haiku children)).
FAST_MODEL_TIMEOUT_DEFAULT = <FAST_MODEL_TIMEOUT_DEFAULT>
# Fast-lane (_grade_bounded) allowance when the fast model is the responder:
# ceil(2 x p95 of a single lean Haiku child).
FAST_LANE_FAST_MODEL_SECONDS = <FAST_LANE_FAST_MODEL_SECONDS>
# Whether codex --output-schema needs additionalProperties:false (measured).
CODEX_SCHEMA_STRICT = <CODEX_SCHEMA_STRICT as True/False>

FAST_MODEL_CWD = "/tmp"
_DISABLE_HOOKS_SETTINGS = '{"disableAllHooks":true}'
# Same billing/routing strip as OrchestratorTools._GRADER_ENV_STRIP (claude children only;
# codex authenticates with its own ChatGPT login and gets the env unmodified).
_ENV_STRIP = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)
_DEFAULT_CLIENTS = (("claude", "claude", "haiku"),)


def fast_model_timeout(hooks_cfg) -> int:
    """`fast_model_timeout_seconds` from the hooks config, else the measured default."""
    value = hooks_cfg.get("fast_model_timeout_seconds") if isinstance(hooks_cfg, Mapping) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value)
    return FAST_MODEL_TIMEOUT_DEFAULT


def fast_model_clients(commander_config) -> list[tuple[str, str, str]]:
    """(client, executable path, Haiku-tier model) in `grader` role order, preferred first.
    Missing or malformed provider config -> Claude Haiku only."""
    try:
        providers = commander_config["providers"]
        role = providers["roles"]["grader"]
        preferred = role["preferred"]
        names = [preferred] + [name for name in role["clients"] if name != preferred]
        out = []
        for name in names:
            client = providers["clients"][name]
            if name not in ("claude", "codex") or not client.get("enabled", False):
                continue
            out.append((name, client.get("path") or name, client["models"]["haiku"]))
        return out or list(_DEFAULT_CLIENTS)
    except (KeyError, TypeError, AttributeError):
        return list(_DEFAULT_CLIENTS)


def claude_fast_argv(path: str, model: str, schema: dict) -> list[str]:
    return [
        path, "-p", "--model", model, "--tools", "", "--no-session-persistence",
        "--strict-mcp-config", "--settings", _DISABLE_HOOKS_SETTINGS,
        "--output-format", "json", "--json-schema", json.dumps(schema),
    ]


def codex_fast_argv(path: str, model: str, schema_file: str) -> list[str]:
    return [
        path, "exec", "-c", 'model_reasoning_effort="low"', "--json", "--ephemeral",
        "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
        "--output-schema", schema_file, "-s", "read-only", "-m", model, "-",
    ]


def parse_claude_output(stdout: str) -> dict | None:
    """FULL structured_output of the type=="result" event (array or single object)."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, list):
        events = [e for e in payload if isinstance(e, dict) and e.get("type") == "result"]
        event = events[-1] if events else None
    elif isinstance(payload, dict) and (payload.get("type") == "result" or "structured_output" in payload):
        event = payload
    else:
        event = None
    out = event.get("structured_output") if event else None
    return out if isinstance(out, dict) else None


def parse_codex_output(stdout: str) -> dict | None:
    """Last item.completed agent_message text, parsed as a JSON object."""
    text = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
    if not isinstance(text, str):
        return None
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _claude_env() -> dict:
    env = dict(os.environ)
    for var in _ENV_STRIP:
        env.pop(var, None)
    return env


def _run_child(argv, *, input_text, timeout, env):
    """The only subprocess boundary (tests stub it). subprocess.run kills the child on timeout."""
    return subprocess.run(
        argv, input=input_text, cwd=FAST_MODEL_CWD, env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def call_fast_model(system_prompt: str, user_prompt: str, schema: dict | None, *,
                    timeout: float, commander_config=None) -> dict:
    """Ask the Haiku-class model. Returns the FULL structured object, or
    {"infrastructure_error": True, "error_detail": ...}. Never raises."""
    schema = schema or {"type": "object"}
    prompt = f"{system_prompt}\n\n{user_prompt}"
    errors = []
    for client, path, model in fast_model_clients(commander_config):
        label = "haiku" if client == "claude" else f"luna:{model}"
        schema_file = None
        try:
            if client == "codex":
                codex_schema = {**schema, "additionalProperties": False} if CODEX_SCHEMA_STRICT else schema
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    json.dump(codex_schema, handle)
                    schema_file = handle.name
                argv, env, parse = codex_fast_argv(path, model, schema_file), dict(os.environ), parse_codex_output
            else:
                argv, env, parse = claude_fast_argv(path, model, schema), _claude_env(), parse_claude_output
            proc = _run_child(argv, input_text=prompt, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            errors.append(f"{label} timed out after {timeout}s")
            continue
        except Exception as exc:  # noqa: BLE001 — a fast-model failure must never crash a grading caller
            errors.append(f"{label} error: {exc}")
            continue
        finally:
            if schema_file is not None:
                try:
                    os.unlink(schema_file)
                except OSError:
                    pass
        if proc.returncode != 0:
            errors.append(f"{label} exited {proc.returncode}")
            continue
        out = parse(proc.stdout)
        if out is None:
            errors.append(f"{label} returned no structured object")
            continue
        missing = [key for key in schema.get("required", []) if key not in out]
        if missing:
            errors.append(f"{label} answer missing required fields {missing}")
            continue
        logger.info("fast model answered via %s", label)
        return out
    detail = "fast model unavailable: " + "; ".join(errors)
    logger.warning(detail)
    return {"infrastructure_error": True, "error_detail": detail}
```

Append to `commander/tests/conftest.py`:

```python


@pytest.fixture(autouse=True)
def _block_fast_model_children(monkeypatch):
    """Never spawn a real claude/codex fast-model child from the suite. With the hooks
    config absent in tests (see _redirect_home), the Commander default backend is the
    Haiku-class fast model; the stub fails like an unavailable model, so the production
    fail-open path runs. Tests of the helper override _run_child themselves."""
    from ironclaude import fast_model

    def _unavailable(argv, *, input_text, timeout, env):
        import subprocess as _subprocess
        return _subprocess.CompletedProcess(argv, 1, "", "fast model disabled in tests")

    monkeypatch.setattr(fast_model, "_run_child", _unavailable)
```

### Step 3: GREEN

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model.py -q > /private/tmp/claude-502/hfm-t3-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/hfm-t3-green.log
```
Expected: `green=0`, with 0 failed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/fast_model.py commander/tests/test_fast_model.py commander/tests/conftest.py
```
Expected: no output.

---

## Task 4: Commander resolver — `haiku` backend and default; schema docs

TDD. This task runs after Task 3, so a half-edited `conftest.py` cannot cause a spurious failure. Its pytest run is limited to its own file.

**Files:**
- Modify: `commander/src/ironclaude/backend_resolver.py` (`:1-8`, `:17`, `:43-81`)
- Modify: `commander/tests/test_backend_resolver.py` (`:30-32`)
- Modify: `worker/config-schema/llm-backend.md` (`:47-50`, `:61-63`, `:107-119`, plus a new section before `## Out of scope` at `:229`)

### Step 1: Test (RED)

Replace `test_unset_backend_defaults_to_ollama_for_grader` (`:30-32`) with:

```python
def test_unset_backend_defaults_to_haiku_for_grader():
    resolved = resolve_backend({}, "grader")
    assert resolved.backend == "haiku"
    assert resolved.url is None


def test_haiku_backend_takes_only_the_spot_model_and_no_local_url():
    cfg = {"backend": "haiku", "ollama": {"url": "http://x", "model": "m", "fallback_url": "http://y"},
           "openai": {"base_url": "http://o/v1", "model": "om", "max_tokens": 900},
           "shadow_model": "legacy", "spots": {"grader": {"model": "spot-m"}}}
    grader = resolve_backend(cfg, "grader")
    assert (grader.backend, grader.model, grader.url, grader.fallback_url, grader.max_tokens) == (
        "haiku", "spot-m", None, None, None)
    shadow = resolve_backend(cfg, "shadow")
    assert (shadow.backend, shadow.model, shadow.url) == ("haiku", None, None)


def test_explicit_ollama_still_resolves_localhost():
    resolved = resolve_backend({"backend": "ollama"}, "grader")
    assert (resolved.backend, resolved.url) == ("ollama", "http://localhost:11434")
```

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_backend_resolver.py -q > /private/tmp/claude-502/hfm-t4-red.log 2>&1; echo "red=$?"; tail -n 6 /private/tmp/claude-502/hfm-t4-red.log
```
Expected:
- `red=1`.
- `test_unset_backend_defaults_to_haiku_for_grader` fails with `AssertionError` (`'ollama' == 'haiku'`).
- `test_haiku_backend_takes_only_the_spot_model_and_no_local_url` fails with `AssertionError` (the url is `http://x`).
- `test_explicit_ollama_still_resolves_localhost` passes.

### Step 2: Implementation (GREEN)

1. In the module docstring (`:6-7`), replace `per-consumer defaults are applied here for Commander (backend default\n"ollama", ollama url default localhost).` with `per-consumer defaults are applied here for Commander (backend default\n"haiku" — the Haiku-class fast model, no local box; ollama url default localhost).`
2. `:17`: `_COMMANDER_DEFAULT_BACKEND = "haiku"`.
3. In `resolve_backend`'s docstring (`:47`), change `?? "ollama"` to `?? "haiku"`.
4. Replace `:61-74` (from `ollama_block = cfg.get("ollama") or {}` through `max_tokens = None` of the else branch) with:
```python
    ollama_block = cfg.get("ollama") or {}
    openai_block = cfg.get("openai") or {}
    if backend == "haiku":
        # Haiku-class fast model: no local box, so no connection fields and no
        # backend-block model (mirrors the shell _resolve_spot `*)` arm).
        block = {}
        model = spot_cfg.get("model")
        url = fallback_url = max_tokens = None
    else:
        block = openai_block if backend == "openai" else ollama_block
        model = spot_cfg.get("model") or _legacy_alias(cfg, spot) or block.get("model")
        if backend == "openai":
            url = openai_block.get("base_url")
            fallback_url = openai_block.get("fallback_base_url")
            max_tokens = openai_block.get("max_tokens")
        else:
            url = ollama_block.get("url") or _OLLAMA_DEFAULT_URL
            fallback_url = ollama_block.get("fallback_url")
            max_tokens = None
```
   The `timeout`/`connect_timeout`/`probe_timeout`/`hook_validation_budget` lines (`:76-81`) stay, and resolve top-level for `haiku` because the block is empty.

### Step 3: `llm-backend.md`

1. In the schema block, after `"hook_validation_budget_seconds": number?` (`:50`, the top-level one), add the line `  "fast_model_timeout_seconds": number?`. The previous line gains a trailing comma.
2. `:61-63`: change `(defaults: shell=haiku, TS=SDK, Commander=ollama)` to `(defaults: shell=haiku, TS=SDK, Commander=haiku)`, and append this sentence: `` `haiku` means the Haiku-class fast model with no local box: no url, and only `spots.S.model` supplies a model. ``
3. Replace the "Consumer defaults (unchanged by this change)" heading and table (`:107-119`) with a "Consumer defaults" table whose Commander rows read `haiku` for the backend. Keep their model defaults, which apply when a spot is set to `ollama`. Replace the paragraph "**This change preserves every one of these defaults.** …" with: `Commander's default backend changed from ollama to haiku on 2026-10-16 (operator decision "Keep it + cover gaps"); a local ollama/openai box stays fully supported through config.`
4. Insert before `## Out of scope` (`:229`) a new section `## Haiku-class fast-model chain` that states:
   - **The order.** Local box (`ollama`/`openai`, 3 s connect / 8 s budget) → Haiku-class model → fail open. `backend: "haiku"` skips the local box. A local result that is empty, failed, non-JSON, or (Commander) any `infrastructure_error`, including an open circuit breaker, falls through.
   - **The per-client mapping.** Hooks: Claude `claude --model haiku`; Codex: the newest installed `gpt-*-luna` from `$CODEX_HOME/models_cache.json` (else `gpt-6-luna`), the same resolution as the advisor broker. Commander: the `grader` role's client order, using each client's configured `models.haiku`, which for Codex is `providers.clients.codex.models.haiku` (`gpt-5.6-luna` by default). The difference is deliberate: the Commander is config-driven.
   - **The isolation flags.** Claude: `--no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true}' --tools ""`, cwd `/tmp`. Codex: `--ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check -s read-only`, `model_reasoning_effort="low"`.
   - **The cap.** `fast_model_timeout_seconds` (top level) caps each fast-model call, default `<FAST_MODEL_TIMEOUT_DEFAULT>`. It replaces `timeout_seconds` on this path. The Commander's fast lane allows `<FAST_LANE_FAST_MODEL_SECONDS>` s extra only when the fast model answers.
   - **The shadow note.** The `shadow` spot needs a local box. It skips quietly (no Slack post) when its backend resolves to `haiku` or the box is unreachable, and it stays skipped under a top-level `haiku` until `spots.shadow.backend` is set.
   - **Also:** the Commander's shared `claude -p` grader argv also carries `--no-session-persistence`, which is safe for every grader tier because `_grader_home` is only the cwd.

### Step 4: GREEN

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_backend_resolver.py -q > /private/tmp/claude-502/hfm-t4-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/hfm-t4-green.log; grep -c "Commander=haiku" /Users/roberthyatt/Code/ironclaude/worker/config-schema/llm-backend.md
```
Expected: `green=0` with 0 failed, and the count is `1`.

### Step 5: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/backend_resolver.py commander/tests/test_backend_resolver.py worker/config-schema/llm-backend.md
```
Expected: no output.

---

## Task 5: Commander wiring — LocalGrader chain, fast lane, summarization, shadow skip, grader argv

TDD.

**Files:**
- Modify: `commander/src/ironclaude/grader.py` (`:56-80`, `:103-123`, `:125-168`, `:170-288`)
- Modify: `commander/src/ironclaude/main.py` (`:62` imports, `:1620`, `:3265-3290`)
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (`:521-527`, `:1023-1049`, `:1491-1503`, `:6507-6607`)
- Modify: `commander/src/ironclaude/shadow_grader.py` (`:18`, `:145-176`, `:300-337`, `:405-409`)
- Create: `commander/tests/test_fast_model_chain.py`
- Modify: `commander/tests/test_grader_routing.py` (append one test)
- Modify, only under the Step 4 rule: `commander/tests/test_grader.py`, `commander/tests/test_local_grader.py`, `commander/tests/test_shadow_grader.py`, `commander/tests/test_orchestrator_mcp.py`, `commander/tests/test_main_operator_wait.py`, `commander/tests/test_daemon.py`, `commander/tests/test_main_validate.py`, `commander/tests/test_stuck_detection.py`, `commander/tests/test_worker_handoff.py`, `commander/tests/test_worker_adapter.py`, `commander/tests/test_enforcement.py`, `commander/tests/test_kill_worker_log_cap.py`, `commander/tests/test_brain_client.py`, `commander/tests/test_grade_schema_bounds.py`

### Step 1: Tests (RED)

Append to `commander/tests/test_grader_routing.py`:
```python


def test_claude_grader_argv_does_not_persist_sessions(tmp_path):
    tools = _tools(tmp_path)
    argv = tools._claude_grader_argv(tools._GRADER_VERDICT_SCHEMA, "/tmp/sys.txt", "opus")
    assert "--no-session-persistence" in argv
```

Create `commander/tests/test_fast_model_chain.py`:

```python
"""Local box -> Haiku-class fast model -> fail open, across the Commander's local-grader spots."""
import json
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from ironclaude import fast_model
from ironclaude.grader import LocalGrader
from ironclaude.ollama_client import OllamaConnectionError, _BREAKERS
from ironclaude.shadow_grader import ShadowGrader

SCHEMA = {"type": "object", "properties": {"valid": {"type": "boolean"}}, "required": ["valid"]}


def _fast_answer(monkeypatch, obj, record=None):
    def fake(argv, *, input_text, timeout, env):
        if record is not None:
            record.append({"argv": argv, "timeout": timeout})
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"type": "result", "structured_output": obj}]), "")
    monkeypatch.setattr(fast_model, "_run_child", fake)


def _write(tmp_path, cfg):
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps(cfg))
    return str(path)


def test_local_connection_error_falls_back_to_full_fast_object(monkeypatch):
    calls = []
    _fast_answer(monkeypatch, {"valid": True, "reason": "kept", "kind": "x"}, calls)
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.side_effect = OllamaConnectionError("refused")
    assert grader.grade("sys", "user", SCHEMA) == {"valid": True, "reason": "kept", "kind": "x"}
    assert calls[0]["timeout"] == fast_model.FAST_MODEL_TIMEOUT_DEFAULT


def test_open_breaker_falls_back_without_a_local_request(monkeypatch, tmp_path):
    _fast_answer(monkeypatch, {"valid": True})
    url = "http://box.invalid:11434"
    cfg = _write(tmp_path, {"backend": "ollama", "ollama": {"url": url, "model": "m"}})
    for _ in range(3):
        _BREAKERS.record_failure(url)
    with patch("requests.post", side_effect=AssertionError("no request while the breaker is open")):
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": True}


def test_local_non_json_falls_back(monkeypatch):
    _fast_answer(monkeypatch, {"valid": False})
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.return_value = "not json"
    assert grader.grade("sys", "user", SCHEMA) == {"valid": False}


def test_haiku_backend_never_builds_a_local_client(monkeypatch, tmp_path):
    cfg = _write(tmp_path, {"backend": "haiku", "fast_model_timeout_seconds": 9})
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    with patch("ironclaude.grader.OllamaClient") as ollama:
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": True}
    ollama.assert_not_called()
    assert calls[0]["timeout"] == 9


def test_local_answer_is_used_without_fast_model(monkeypatch):
    called = []
    _fast_answer(monkeypatch, {"valid": False}, called)
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.return_value = '{"valid": true}'
    assert grader.grade("sys", "user", SCHEMA) == {"valid": True}
    assert called == []


def test_both_failing_keeps_local_detail_and_names_fast_model():
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.side_effect = OllamaConnectionError("refused")
    result = grader.grade("sys", "user", SCHEMA)
    assert result["infrastructure_error"] is True
    assert "refused" in result["error_detail"]
    assert "fast model unavailable" in result["error_detail"]


def test_client_selection_failure_falls_back_to_fast_model(monkeypatch, tmp_path):
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    cfg = _write(tmp_path, {"backend": "openai"})  # no openai.base_url: OpenAiClient raises
    with patch("requests.post", side_effect=AssertionError("no local request without a client")):
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": True}
    assert len(calls) == 1


def test_client_selection_failure_with_fast_model_down_returns_infrastructure_error(monkeypatch, tmp_path):
    monkeypatch.setattr(fast_model, "_run_child",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", ""))
    cfg = _write(tmp_path, {"backend": "openai"})
    result = LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA, read_timeout=2)
    assert result["infrastructure_error"] is True
    assert "base_url" in result["error_detail"]
    assert "fast model unavailable" in result["error_detail"]


def test_profile_error_does_not_reach_the_fast_model(monkeypatch):
    from ironclaude.communication_profiles import CommunicationProfileError
    called = []
    _fast_answer(monkeypatch, {"valid": True}, called)
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    with patch("ironclaude.grader.apply_communication_profile", side_effect=CommunicationProfileError("missing")):
        result = grader.grade("sys", "user", SCHEMA)
    assert result == {"infrastructure_error": True, "error_detail": "missing"}
    assert called == []


def test_fast_lane_uses_fast_lane_bound_and_signals(monkeypatch, tmp_path):
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    cfg = _write(tmp_path, {"backend": "haiku"})
    signalled = []
    out = LocalGrader(config_path=cfg).grade("s", "u", SCHEMA, read_timeout=2,
                                             on_fast_model=lambda: signalled.append(True))
    assert out == {"valid": True}
    assert signalled == [True]
    assert calls[0]["timeout"] == fast_model.FAST_LANE_FAST_MODEL_SECONDS


def test_grader_role_order_reaches_codex(monkeypatch, tmp_path):
    calls = []
    def fake(argv, *, input_text, timeout, env):
        calls.append(argv[0])
        text = json.dumps({"valid": True})
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
        return subprocess.CompletedProcess(argv, 0, line, "")
    monkeypatch.setattr(fast_model, "_run_child", fake)
    commander_cfg = {"providers": {
        "clients": {"claude": {"enabled": True, "path": "claude", "models": {"haiku": "haiku"}},
                    "codex": {"enabled": True, "path": "codex", "models": {"haiku": "gpt-5.6-luna"}}},
        "roles": {"grader": {"preferred": "codex", "clients": ["codex"]}}}}
    cfg = _write(tmp_path, {"backend": "haiku"})
    assert LocalGrader(config_path=cfg, commander_config=commander_cfg).grade("s", "u", SCHEMA) == {"valid": True}
    assert calls == ["codex"]


# ── _grade_bounded: the bound grows only while the fast model answers ──

def _daemon(timeout):
    from ironclaude.main import IroncladeDaemon
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._grade_state_lock = threading.Lock()
    daemon._grade_in_flight = False
    daemon.config = {"fast_lane_grade_timeout_seconds": timeout}
    return daemon


def test_grade_bounded_extends_bound_for_fast_model(monkeypatch):
    monkeypatch.setattr(fast_model, "FAST_LANE_FAST_MODEL_SECONDS", 2.0)
    daemon = _daemon(0.2)

    def slow_fast(system, user, schema, read_timeout=None, on_fast_model=None):
        on_fast_model()
        time.sleep(0.6)
        return {"waiting_on": "operator"}

    daemon._grader.grade.side_effect = slow_fast
    assert daemon._grade_bounded("s", "u", {}) == {"waiting_on": "operator"}


def test_grade_bounded_keeps_bound_for_local(monkeypatch):
    monkeypatch.setattr(fast_model, "FAST_LANE_FAST_MODEL_SECONDS", 2.0)
    daemon = _daemon(0.2)

    def slow_local(system, user, schema, read_timeout=None, on_fast_model=None):
        time.sleep(0.6)
        return {"waiting_on": "operator"}

    daemon._grader.grade.side_effect = slow_local
    assert daemon._grade_bounded("s", "u", {}) is None


# ── shadow grader skips quietly ──

def test_shadow_skips_under_haiku_without_building_a_client(tmp_path):
    cfg = _write(tmp_path, {"backend": "haiku"})
    with patch("ironclaude.shadow_grader.OllamaClient") as ollama:
        result = ShadowGrader(config_path=cfg).grade_with_tools("s", "u", repo_path=str(tmp_path))
    ollama.assert_not_called()
    assert result["skipped"] is True and result["infrastructure_error"] is True


def test_shadow_marks_unreachable_box():
    grader = ShadowGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_chat.side_effect = OllamaConnectionError("refused")
    assert grader.grade_with_tools("s", "u", repo_path="/tmp")["unreachable"] is True


def _tools(tmp_path, hooks_cfg):
    import ironclaude.orchestrator_mcp as omcp
    from ironclaude.db import init_db
    tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(),
                                   db_conn=init_db(str(tmp_path / "c.db")), config={})
    tools._ollama_config_path = _write(tmp_path, hooks_cfg)
    return tools


@pytest.mark.parametrize("shadow", [
    {"infrastructure_error": True, "skipped": True, "error_detail": "haiku", "tool_calls": []},
    {"infrastructure_error": True, "unreachable": True, "error_detail": "refused", "tool_calls": []},
])
def test_shadow_report_posts_nothing_when_skipped_or_unreachable(tmp_path, shadow):
    tools = _tools(tmp_path, {"backend": "ollama"})
    tools._slack = MagicMock()
    tools._shadow_grader = MagicMock()
    tools._shadow_grader.grade_with_tools.return_value = shadow
    tools._run_shadow_and_report("ctx", "w1", "/tmp", {"grade": "A", "approved": True, "feedback": ""}, [], "s", "u")
    tools._slack.post_message.assert_not_called()


# ── session summarization ──

def _session(tools):
    tools.tmux.list_sessions.return_value = ["my-session"]
    tools.tmux.list_pane_pid.return_value = "1"
    tools.tmux.pane_current_command.return_value = "node"
    tools.tmux.capture_pane.return_value = "terminal content"


def test_summarization_haiku_branch(monkeypatch, tmp_path):
    _fast_answer(monkeypatch, {"summary": "S"})
    tools = _tools(tmp_path, {"backend": "haiku"})
    _session(tools)
    tools._get_ollama_client = MagicMock(side_effect=AssertionError("no local box under haiku"))
    assert json.loads(tools.list_claude_sessions())[0]["summary"] == "S"


def test_summarization_haiku_failure_is_explicit(tmp_path):
    tools = _tools(tmp_path, {"backend": "haiku"})
    _session(tools)
    summary = json.loads(tools.list_claude_sessions())[0]["summary"]
    assert summary.startswith("ERROR: Haiku-class summarization failed")


def test_summarization_ollama_unavailable_chains_to_fast_model(monkeypatch, tmp_path):
    from ironclaude.ollama_client import OllamaError
    _fast_answer(monkeypatch, {"summary": "S"})
    tools = _tools(tmp_path, {"backend": "ollama"})
    _session(tools)
    client = MagicMock()
    client.get_ps.side_effect = OllamaError("refused")
    tools._get_ollama_client = MagicMock(return_value=client)
    assert json.loads(tools.list_claude_sessions())[0]["summary"] == "S"
    client.post_generate.assert_not_called()
```

RED run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model_chain.py tests/test_grader_routing.py -q > /private/tmp/claude-502/hfm-t5-red.log 2>&1; echo "red=$?"; tail -n 30 /private/tmp/claude-502/hfm-t5-red.log
```
Expected: `red=1`. These fail with `AssertionError` (an `infrastructure_error` dict, or `None`, instead of the fast object; a missing key):
- `test_claude_grader_argv_does_not_persist_sessions`
- `test_local_connection_error_falls_back_to_full_fast_object`
- `test_open_breaker_falls_back_without_a_local_request`
- `test_local_non_json_falls_back`
- `test_both_failing_keeps_local_detail_and_names_fast_model`
- `test_client_selection_failure_falls_back_to_fast_model`
- `test_client_selection_failure_with_fast_model_down_returns_infrastructure_error` (today `grade(..., read_timeout=2)` already exists and returns an `infrastructure_error` without the "fast model unavailable" text)
- the `_grade_bounded` extend test
- the shadow and summarization tests

These fail with `TypeError` (unexpected keyword argument `on_fast_model` or `commander_config`):
- `test_fast_lane_uses_fast_lane_bound_and_signals`
- `test_grader_role_order_reaches_codex`

These pass:
- `test_local_answer_is_used_without_fast_model`
- `test_profile_error_does_not_reach_the_fast_model`
- `test_grade_bounded_keeps_bound_for_local`

Record the measured list.

### Step 2: Implementation (GREEN)

**`grader.py`**
1. Add `from ironclaude import fast_model` after the `backend_resolver` import (`:19`).
2. `__init__` (`:56-57`) gains the keyword `commander_config=None`. Store `self._commander_config = commander_config` and `self._full_cfg: dict = {}` (the whole hooks config, for `fast_model_timeout_seconds`).
3. `_build_client` (`:103-123`): first line `if resolved.backend == "haiku": return None, "haiku", None, None, cfg`, so no local client is ever built for `haiku`.
4. `_get_client` (`:133`) and `_get_fast_client_state` (`:160`): after `cfg = self._read_config()`, add `self._full_cfg = cfg`.
5. `grade` gains the keyword `on_fast_model=None` (signature `:170-177`; document it in the docstring). Replace `:198-288`, everything after the profile block (`:191-196`), which stays unchanged and still returns early, with:
```python
        try:
            if read_timeout is None:
                client = self._get_client()
                state = (self._backend, self._openai_model, self._openai_max_tokens,
                         self._cfg, self._spot_model, self._spot_thinking)
            else:
                client, *state = self._get_fast_client_state(read_timeout)
        except OllamaError as e:
            # No usable local client (e.g. backend "openai" with no base_url,
            # openai_client.py:47-48). Nothing else is bound yet: go straight to the
            # Haiku-class step, keeping this detail for the both-failed case.
            return self._fast_model_step(profiled_system_prompt, user_prompt, schema,
                                         read_timeout, on_fast_model, str(e))
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
   Move the payload/post/parse code (`:214-288`, unchanged) into the new method `_grade_local(self, client, backend, openai_model, openai_max_tokens, cfg, spot_model, spot_thinking, profiled_system_prompt, user_prompt, schema) -> dict`. `self._full_cfg` is valid on the `except` path, because item 4 sets it right after `_read_config()`, before `_build_client` can raise.
6. Add:
```python
    def _fast_model_step(self, system_prompt, user_prompt, schema, read_timeout, on_fast_model, local_detail):
        """R1 chain step: the Haiku-class model after the local box (or instead of it
        under backend "haiku"). Keeps the local failure detail when both fail."""
        if on_fast_model is not None:
            on_fast_model()
        timeout = (fast_model.FAST_LANE_FAST_MODEL_SECONDS if read_timeout is not None
                   else fast_model.fast_model_timeout(self._full_cfg))
        result = fast_model.call_fast_model(system_prompt, user_prompt, schema, timeout=timeout,
                                            commander_config=self._commander_config)
        if result.get("infrastructure_error") and local_detail is not None:
            return self._build_infrastructure_error(f"{local_detail}; {result['error_detail']}")
        return result
```

**`main.py`**
1. Add `from ironclaude import fast_model` next to the `grader` import (`:62`).
2. `:1620` becomes `self._grader = LocalGrader(keep_alive="30m", commander_config=self.config)`.
3. In `_grade_bounded`: after `box: dict = {}` (`:3265`), add `fast_started = threading.Event()`. The worker call (`:3269`) becomes `self._grader.grade(system, user, schema, read_timeout=timeout, on_fast_model=fast_started.set)`. After `t.join(timeout)` (`:3284`), insert:
```python
        if t.is_alive() and fast_started.is_set():
            # The Haiku-class model is the responder: extend by the measured fast-lane
            # allowance. If the fast step starts only after this bound expired (a local
            # stall >= the bound), the result is None exactly as before.
            t.join(fast_model.FAST_LANE_FAST_MODEL_SECONDS)
```
4. `_detect_worker_prompt` (`:4227-4262`): no change, because the Task 1 gate `PROMPT_DETECTION_SYNC=yes` keeps it synchronous. It gets the chain through `LocalGrader.grade`.

**`orchestrator_mcp.py`**
1. `:521` → `LocalGrader(config_path=self._ollama_config_path, commander_config=self._config)`. Inside `:523-527`, add `commander_config=self._config,`.
2. `_claude_grader_argv` (`:1023-1049`): insert `"--no-session-persistence",` after `"--strict-mcp-config",` (`:1044`). Replace the docstring's "Kept byte-identical to the historical command" sentence with: "Historical command plus --no-session-persistence (no session file per grade; safe for every tier because _grader_home is only the cwd)."
3. `_run_shadow_and_report`: after the `grade_with_tools(...)` call (`:1493-1495`), insert:
```python
            if shadow_result.get("skipped") or shadow_result.get("unreachable"):
                # The shadow is a concordance check of the LOCAL model: with no local box
                # (backend haiku) or an unreachable one there is nothing to compare.
                logger.info("Shadow grader skipped for %s/%s: %s", context, worker_id,
                            shadow_result.get("error_detail"))
                return
```
4. Session summarization (`list_claude_sessions`):
   - Move the prompt text (`:6578-6586`) into a helper `_session_summary_prompt(name, sample_large) -> str`.
   - Add `_SESSION_SUMMARY_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}` and this method:
```python
    def _fast_model_session_summary(self, name: str, sample_large: str, hooks_cfg: dict) -> tuple[str | None, str]:
        """Haiku-class summary. Returns (summary, "") or (None, error detail)."""
        try:
            prompt = apply_communication_profile("session_summarizer", self._session_summary_prompt(name, sample_large))
        except CommunicationProfileError as e:
            return None, f"communication-profile infrastructure error — {e}"
        result = fast_model.call_fast_model("", prompt, self._SESSION_SUMMARY_SCHEMA,
                                            timeout=fast_model.fast_model_timeout(hooks_cfg),
                                            commander_config=self._config)
        if result.get("infrastructure_error"):
            return None, result.get("error_detail", "unknown")
        return str(result["summary"]), ""
```
   - Add `from ironclaude import fast_model` to the imports (after `:52`).
   - In the backend branch (`:6532-6552`), insert `elif summ_backend == "haiku": summ_fast_only = True` between the openai and else branches, with `summ_fast_only = False` initialised next to `summ_backend` (`:6522`).
   - Replace the per-session block (`:6573-6607`) with:
```python
            if summ_fast_only:
                if not sample_large:
                    summary = "ERROR: no pane content to summarize"
                else:
                    fast_summary, detail = self._fast_model_session_summary(name, sample_large, _summ_cfg)
                    summary = fast_summary if fast_summary is not None else f"ERROR: Haiku-class summarization failed — {detail}"
            elif not ollama_available:
                summary = "ERROR: Ollama unavailable — summary not generated"
                if sample_large:
                    fast_summary, _ = self._fast_model_session_summary(name, sample_large, _summ_cfg)
                    summary = fast_summary if fast_summary is not None else summary
            elif not sample_large:
                summary = "ERROR: no pane content to summarize"
            else:
                <existing local try/except block, :6587-6607, with the prompt from
                 self._session_summary_prompt(name, sample_large); in the
                 `except OllamaError as e:` arm, after the existing summary string,
                 add: fast_summary, _ = self._fast_model_session_summary(name, sample_large, _summ_cfg);
                 summary = fast_summary if fast_summary is not None else summary>
```
   When the fast model also fails, the existing local error strings are kept.
   - Keep the `# IRONCLAUDE_LLM_PATH: session_summarizer` comment (`:6588`) exactly once, inside the retained local block. Add no `IRONCLAUDE_LLM_PATH` marker in `_fast_model_session_summary` or in `fast_model.py`: `commander/tests/test_communication_construction_inventory.py:19-40` pins `session_summarizer: 1` and `local_grader: 1`, and that test file is not in `allowed_files`.

**`shadow_grader.py`**
1. Import `OllamaConnectionError` alongside `OllamaClient, OllamaError` (`:18`).
2. `_get_client` (`:145-176`): after `self._model = …` (`:155`), add `if self._backend == "haiku": return None`, so no client is built and `_client` stays `None`.
3. `grade_with_tools`: after `client = self._get_client()` (`:300-303`), add:
```python
        if self._backend == "haiku":
            return {**self._build_error(
                "shadow spot resolves to backend 'haiku': the shadow grader needs a local box; "
                "set spots.shadow.backend"), "skipped": True}
```
4. In both `except OllamaError as e:` arms (`:336-337`, `:408-409`), build the error first. Set `err["unreachable"] = True` when `isinstance(e, OllamaConnectionError)` (this includes an open breaker), then return `err`.

### Step 3: GREEN for the new tests

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_fast_model_chain.py tests/test_grader_routing.py tests/test_fast_model.py tests/test_backend_resolver.py -q > /private/tmp/claude-502/hfm-t5-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/hfm-t5-green.log
```
Expected: `green=0`, with 0 failed.

### Step 4: Full suite and the default-flip rule

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/hfm-t5-suite1.log 2>&1; echo "suite1=$?"; grep -E "^(FAILED|ERROR)" /private/tmp/claude-502/hfm-t5-suite1.log
```
Expected: some failures are possible. Each must be explained by the operator-approved default flip: the test relied on an absent hooks config, or one without `backend`, resolving to `ollama`. Fix such a test **only** by making `ollama` explicit, merging `"backend": "ollama"` into its config dict, or writing a temp config `{"backend": "ollama", …}` and pointing `_config_path`/`_ollama_config_path` at it. Do not change any other assertion.

These tests are known from source to need it:
- `test_grader.py`:
  - `TestConfigLoading.test_config_absent_uses_localhost_defaults` (`:212-223`): rename it to `test_ollama_backend_without_url_uses_localhost_defaults`, write `{"backend": "ollama"}`, and keep the assertion.
  - `TestFastLaneReadTimeout` `:296-344`: write `{"backend": "ollama"}` instead of `nonexistent.json`.
- `test_shadow_grader.py`:
  - `test_default_timeout_is_600_when_config_missing` (`:555-560`): rename it to `test_default_timeout_is_600_when_ollama_config_has_no_timeout` and write `{"backend": "ollama"}`.
  - `:562-581`: add `"backend": "ollama"` to both configs.
- `test_local_grader.py`:
  - `test_successful_grading` (`:103-116`): point `tools._local_grader._config_path` at `ollama_config`.
  - `test_config_file_missing` (`:201-213`): rename it to `test_ollama_backend_without_url_uses_localhost` and use `{"backend": "ollama"}`.
- `test_orchestrator_mcp.py` session-summary tests `:9880-10076`: point `tools._ollama_config_path` at a temp `{"backend": "ollama", …}` config, merging into the existing config where a test writes one.
- `test_daemon.py` `TestGradeBoundedFastLaneReadTimeout.test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout` (`:5645`): replace `absent.json` with a written `tmp_path / "ollama.json"` containing `{"backend": "ollama"}`, and pass that path. Otherwise `haiku` reaches the stubbed fast model, which answers instantly, and the `is None` assertion fails.
- `test_local_grader.py` `:151-199` (five tests): add `tools._local_grader._config_path = ollama_config` after `tools._ollama_config_path = ollama_config`. The fixture's `validation_backend: ollama` makes `ollama` explicit; without the line, the grader reads the absent default path.

Any failure that this rule cannot fix → STOP and report it with its traceback.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/hfm-t5-suite2.log 2>&1; echo "suite2=$?"; tail -n 3 /private/tmp/claude-502/hfm-t5-suite2.log
```
Expected: `suite2=0`, with 0 failed. Any remaining failure must be shown pre-existing in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 5: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/grader.py commander/src/ironclaude/main.py commander/src/ironclaude/orchestrator_mcp.py commander/src/ironclaude/shadow_grader.py commander/tests/test_fast_model_chain.py commander/tests/test_grader_routing.py; git -C /Users/roberthyatt/Code/ironclaude status --short commander/tests
```
Then stage each Step 4 test file that `status --short` shows as modified, by its exact path, using `git -C /Users/roberthyatt/Code/ironclaude add <path>`.
Expected: no output from `add`. The `status` lists only files from this task's allowed list.

---

## Task 6: Episodic summarizer — fresh SDK calls run the system `claude` with `--no-session-persistence`

TDD.

The operator chose "Use system claude CLI" on 2026-10-16. The installed `@anthropic-ai/claude-agent-sdk` is 0.1.42. Neither its `sdk.d.ts` nor its vendored `cli.js` mentions `persistSession` or `no-session-persistence` (planning-time grep: 0 matches in each). The SDK does accept the following (verified):
- `extraArgs?: Record<string, string | null>` (`sdk.d.ts:475`). Each entry becomes `--<flag>` when the value is null (`sdk.mjs:6506-6510`).
- `pathToClaudeCodeExecutable?: string` (`sdk.d.ts:489`). A path without a `.js`/`.mjs`/`.ts`/`.tsx`/`.jsx` extension is spawned directly as a native binary (`sdk.mjs:6517-6523`, `isNativeBinary` `:6733-6736`).

The system `claude` (2.1.302) supports `--no-session-persistence`.

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/summarizer.ts` (add `resolveSystemClaude` after the imports at `:1-6`; change the `query` options at `:288-300`)
- Modify: `worker/mcp-servers/episodic-memory/src/summarizer.test.ts` (append)

### Step 1: Test (RED)

Append to `summarizer.test.ts`. It uses the file's existing `writeIcOllamaConfig` helper (`:42`) and vitest imports (`:1-5`).
```ts
describe('callClaude fresh calls do not persist sessions', () => {
  afterEach(() => {
    delete process.env.EPISODIC_MEMORY_CLAUDE_PATH;
    vi.doUnmock('@anthropic-ai/claude-agent-sdk');
  });

  function mockQuery(seen: any[]) {
    vi.doMock('@anthropic-ai/claude-agent-sdk', () => ({
      query: ({ options }: any) => {
        seen.push(options);
        return (async function* () { yield { type: 'result', result: '<summary>ok</summary>' }; })();
      },
    }));
  }

  it('runs the system claude with --no-session-persistence on a fresh call, not on resume', async () => {
    vi.resetModules();
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'summ-claude-'));
    const fakeClaude = path.join(dir, 'claude');
    fs.writeFileSync(fakeClaude, '#!/bin/sh\nexit 0\n');
    fs.chmodSync(fakeClaude, 0o755);
    process.env.EPISODIC_MEMORY_CLAUDE_PATH = fakeClaude;
    const seen: any[] = [];
    mockQuery(seen);
    writeIcOllamaConfig({});
    const { callClaude } = await import('./summarizer.js');
    await callClaude('fresh');
    await callClaude('resumed', 'session-1');
    expect(seen[0].pathToClaudeCodeExecutable).toBe(fakeClaude);
    expect(seen[0].extraArgs).toEqual({ 'no-session-persistence': null });
    expect('extraArgs' in seen[1]).toBe(false);
    expect('pathToClaudeCodeExecutable' in seen[1]).toBe(false);
  });

  it('keeps the bundled CLI and warns when no system claude is found', async () => {
    vi.resetModules();
    process.env.EPISODIC_MEMORY_CLAUDE_PATH = path.join(os.tmpdir(), 'no-such-claude-binary');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const seen: any[] = [];
    mockQuery(seen);
    writeIcOllamaConfig({});
    const { callClaude } = await import('./summarizer.js');
    await callClaude('fresh');
    expect('extraArgs' in seen[0]).toBe(false);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('no system claude'));
    warn.mockRestore();
  });
});
```
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx vitest run src/summarizer.test.ts > /private/tmp/claude-502/hfm-t6-red.log 2>&1; echo "red=$?"; tail -n 20 /private/tmp/claude-502/hfm-t6-red.log
```
Expected: `red=1`.
- The first new test fails on `expect(seen[0].pathToClaudeCodeExecutable).toBe(fakeClaude)`, receiving `undefined`.
- The second fails on the `warn` assertion.
- Pre-existing tests still pass.

### Step 2: Implementation (GREEN)

1. After the imports (`:1-6`), add:
```ts
// Fresh summarizer calls must not leave a session file behind (operator, 2026-10-12).
// SDK 0.1.42 has no persistSession option, and its vendored cli.js lacks the flag, so
// fresh calls run the system `claude` (which has --no-session-persistence) instead.
export function resolveSystemClaude(): string | null {
  const override = process.env.EPISODIC_MEMORY_CLAUDE_PATH;
  const candidates = override
    ? [override]
    : (process.env.PATH || '').split(path.delimiter).filter(Boolean).map((dir) => path.join(dir, 'claude'));
  for (const candidate of candidates) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {
      // Not here; try the next PATH entry.
    }
  }
  return null;
}
```
2. Before the `for await` at `:288`, add:
```ts
  const systemClaude = sessionId ? null : resolveSystemClaude();
  if (!sessionId && !systemClaude) {
    console.warn('summarizer: no system claude found on PATH; fresh call uses the bundled CLI and may persist a session');
  }
```
3. Replace the spread at `:297-299` with:
```ts
      ...(sessionId ? {} : {
        systemPrompt: 'Write concise, factual summaries. Output ONLY the summary - no preamble, no "Here is", no "I will". Your output will be indexed directly.',
        // The resume path keeps persistence: it needs the session it resumes.
        ...(systemClaude ? {
          pathToClaudeCodeExecutable: systemClaude,
          extraArgs: { 'no-session-persistence': null },
        } : {})
      })
```

### Step 3: GREEN plus build

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx vitest run src/summarizer.test.ts > /private/tmp/claude-502/hfm-t6-green.log 2>&1; echo "green=$?"; tail -n 3 /private/tmp/claude-502/hfm-t6-green.log; npm run build > /private/tmp/claude-502/hfm-t6-build.log 2>&1; echo "build=$?"; tail -n 3 /private/tmp/claude-502/hfm-t6-build.log
```
Expected: `green=0` and `build=0`. `dist/` is gitignored (`.gitignore:29`) and is not staged.

### Step 4: Live proof — the old SDK drives the new CLI, and no session file is written

This checks two things:
- that SDK 0.1.42's stream protocol works against CLI 2.1.302;
- that the flag takes effect.

The script runs from its own scratch cwd, so a persisted session could only land in that cwd's projects directory.
```bash
mkdir -p /private/tmp/claude-502/hfm-t6-live && cd /private/tmp/claude-502/hfm-t6-live && node --input-type=module -e "import('/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/dist/summarizer.js').then(async (m) => { const out = await m.callClaude('Summarize in five words: the cat sat on the mat.'); console.log('SUMMARY=' + JSON.stringify(out)); })" > /private/tmp/claude-502/hfm-t6-live.log 2>&1; echo "live=$?"; tail -n 5 /private/tmp/claude-502/hfm-t6-live.log; find /Users/roberthyatt/.claude/projects/-private-tmp-claude-502-hfm-t6-live -name '*.jsonl' > /private/tmp/claude-502/hfm-t6-sessions.log 2>&1; echo "find=$?"; cat /private/tmp/claude-502/hfm-t6-sessions.log
```
Expected:
- `live=0`, with a `SUMMARY="…"` line holding a non-empty summary.
- Either `find=1` with "No such file or directory" (the projects directory was never created), or `find=0` with an empty list. Either one means no session file.

If `dist/summarizer.js` is not the built entry, use the path printed by the Step 3 build log. If the call errors or a `.jsonl` is listed, STOP and report to the operator. Do not fall back to persisting silently.

**Resume path (design R7b).** The resume path (`:372`) is dead in practice. All six callers call `summarizeConversation(exchanges)` with no `sessionId`: `codex-sync.ts:148`, `verify.ts:162`, `indexer.ts:149`, `:238`, `:350` and `sync.ts:221` (planning-time grep). So no call resumes a session, and no-persistence on fresh calls cannot break a resume. The resume branch keeps its current behavior.

### Step 5: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/summarizer.ts worker/mcp-servers/episodic-memory/src/summarizer.test.ts
```
Expected: no output.

---

## Task 7: README — recommend Haiku-class models and describe the chain

No tests required: documentation.

**Files:**
- Modify: `README.md` (`:247-258`)

### Step 1: Edit

Replace the "### Validation Backend (Recommended)" section (`:247-258`) with a section titled `### Validation Backend: Haiku-Class Models (Recommended)`. It states:
- **The recommendation.** Haiku-class models are recommended: Claude Haiku in Claude sessions and the newest installed Codex Luna in Codex sessions, because they are cheap, good and need no setup. Running your own local model is optional.
- **The chain.** Configured local box (Ollama or an OpenAI-compatible endpoint) → Haiku-class model → fail open with a non-blocking "unavailable" note. `backend: "haiku"`, the default for both the hooks and the Commander, skips the local box.
- **Sessions.** Haiku-class children leave no session files (Claude `--no-session-persistence`, Codex `--ephemeral`) and run without IronClaude hooks or MCP servers.
- **The cap.** `fast_model_timeout_seconds` (default `<FAST_MODEL_TIMEOUT_DEFAULT>`) caps each fast-model call.
- **Shadow.** The Commander's shadow grader needs a local box: it is skipped under `haiku` until `spots.shadow.backend` is set.
- **Local hosting**, kept as an optional subsection: the existing `ollama pull` / `/ironclaude:setup-ollama-validation` block, unchanged.
- **Links** to `worker/config-schema/llm-backend.md` for the full schema.

### Step 2: Verify

```bash
grep -n -e "Haiku-Class Models (Recommended)" -e "fast_model_timeout_seconds" -e "setup-ollama-validation" /Users/roberthyatt/Code/ironclaude/README.md > /private/tmp/claude-502/hfm-t7.log 2>&1; echo "readme=$?"; cat /private/tmp/claude-502/hfm-t7.log
```
Expected: `readme=0`. The heading appears once and `fast_model_timeout_seconds` appears at least once in it. `setup-ollama-validation` still appears in the section and in the skills table (`:324`).

### Step 3: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add README.md
```
Expected: no output.

---

## Task 8: Full regression

No tests required beyond the runs below; this is the regression run.

**Files:**
- Stage only: `docs/plans/2026-10-16-haiku-class-fast-model-fallback-design.md`, `docs/plans/2026-10-16-haiku-class-fast-model-fallback.md`, `docs/plans/2026-10-16-haiku-class-fast-model-fallback.plan.json`, `docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md`

### Step 1: Commander suite

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/hfm-t8-commander.log 2>&1; echo "commander=$?"; tail -n 3 /private/tmp/claude-502/hfm-t8-commander.log
```
Expected: `commander=0`, with 0 failed.

### Step 2: Hook suites (with the sentinel from Task 2 first on PATH)

```bash
rm -f /private/tmp/claude-502/hfm-sentinel/calls.log; PATH=/private/tmp/claude-502/hfm-sentinel/bin:$PATH make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/hfm-t8-hooks.log 2>&1; echo "hooks=$?"; tail -n 3 /private/tmp/claude-502/hfm-t8-hooks.log; ls -la /private/tmp/claude-502/hfm-sentinel/calls.log
```
Expected: `hooks=0`. Record `calls.log`, if present, as in Task 2.

### Step 3: Episodic-memory suite

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx vitest run > /private/tmp/claude-502/hfm-t8-episodic.log 2>&1; echo "episodic=$?"; tail -n 5 /private/tmp/claude-502/hfm-t8-episodic.log
```
Expected: `episodic=0`. Any failure must be shown pre-existing in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 4: Stage the plan docs

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-haiku-class-fast-model-fallback-design.md docs/plans/2026-10-16-haiku-class-fast-model-fallback.md docs/plans/2026-10-16-haiku-class-fast-model-fallback.plan.json docs/plans/2026-10-16-haiku-class-fast-model-fallback-findings.md; git -C /Users/roberthyatt/Code/ironclaude status --short
```
Expected: no output from `add`. `status` shows every file staged by Tasks 1–8, plus the pre-existing unrelated ` M worker/.codex-plugin/plugin.json`, which is not staged.

---

## Post-commit deploy and live checks (operator, not plan tasks)

After the operator commits, deploy per R8:
1. `make -C /Users/roberthyatt/Code/ironclaude codex-plugin-release` (Codex plugin) and `make -C /Users/roberthyatt/Code/ironclaude deploy-hooks` (stable hook dir and Claude plugin cache).
2. Copy `worker/hooks/hooks.json` and sync `worker/skills/` into the Claude plugin cache version directory.
3. In the installed episodic-memory plugin copy, run `npm run build`. `cli/mcp-server-wrapper.js` rebuilds `dist/` only when files are missing (`:63`, `:83`), so an existing old bundle would otherwise keep running.
4. Restart the Commander (`make stop`, then relaunch with the Claude Code env vars unset, detached; see memory `project_restart_commander_strip_claudecode`) and ChatGPT.app (kill the exact PID; see `feedback_codex_restart_no_graceful_quit`).

Live checks:
- **(a) Claude.** End a turn in a Claude session that fires the Stop hook. The hook's system message shows graded checks (letters, not "evaluation unavailable"). A `find /Users/roberthyatt/.claude/projects/-private-tmp -newer <marker> -type f` taken around that Stop shows no file whose session id belongs to the hook's children (use a marker file created just before the Stop).
- **(b) Codex.** A real Codex Stop hook is graded, and the hook log shows `VALIDATION_LLM_BACKEND`/`luna:<NEWEST_LUNA>` where a direct caller logs it. `find /Users/roberthyatt/.codex/sessions -newer <marker> -type f` shows no new rollout from the hook's child.
- **(c) Commander.** With `backend: "haiku"`, a `send_to_worker` grading or a Brain-message validation returns a real verdict, and the Commander log shows `fast model answered via haiku`. No shadow-grader Slack post appears.
