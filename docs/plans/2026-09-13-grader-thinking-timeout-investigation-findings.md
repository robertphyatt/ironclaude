# Grader Circuit-Breaker Trip Against amd-halo — Live Investigation Findings

> **Recorded:** 2026-09-13, Task 1 execution
> Evidence source: live commands run directly in this session (Bash tool output below). Explicitly excludes `/tmp/ic/daemon.log` narration and the episodic-memory archive result surfaced earlier in this session's brainstorming phase — both flagged inadmissible/fabricated.

## Step 1: Live Commander daemon process

```
roberthyatt      25173   0.0  0.2 435477680  82848   ??  SN    6:06PM   0:16.21 .venv/bin/python -u -m ironclaude.main
roberthyatt      25027   0.0  0.0 435300176    944   ??  SN    6:06PM   0:00.00 /bin/sh -c .venv/bin/pip install -qe . && set -a && . ./.env && set +a && .venv/bin/python -u -m ironclaude.main 2>&1 | tee -a /tmp/ironclaude-daemon.log
```
PID 25173, started 6:06PM. The parent shell process (25027) is running the exact `commander/Makefile:16` `run:` target verbatim (including its `tee -a /tmp/ironclaude-daemon.log` — a different, genuine log path from the `/tmp/ic/daemon.log` flagged as suspicious earlier). `lsof -a -p 25173 -d cwd -Fn` confirms the process's cwd is `/Users/roberthyatt/Code/ironclaude/commander` — the **primary checkout**, not this worktree.

## Step 2: Primary checkout HEAD

```
$ git worktree list --porcelain
worktree /Users/roberthyatt/Code/ironclaude
HEAD 255d5949d7eeba9856bbf6c65c5205475129e3ca
branch refs/heads/main
...
worktree /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c
HEAD 255d5949d7eeba9856bbf6c65c5205475129e3ca
branch refs/heads/ironclaude/352afa8c-9e57-42c1-bada-de222093de5c
```
Primary checkout HEAD `255d5949d7...` matches this worktree's `255d594` exactly. **Confirmed: the live daemon is genuinely running v1.1.10 code** — including the thinking-off default (v1.1.9) and the three-state busy/down breaker (v1.1.10) — not a stale pre-restart process.

## Step 3: amd-halo reachability

```
$ curl -sS -m 5 -w "\nhttp_code=%{http_code} time_total=%{time_total}\n" http://amd-halo:8080/v1/models
{"data":[{"id":"gemma4-26b-a4b","object":"model","created":1789353400,"owned_by":"llama-swap","meta":{"llamaswap":{"type":"model"}},"status":{"value":"loaded"}},{"id":"qwen3.8-27b","object":"model","created":1789353400,"owned_by":"llama-swap","meta":{"llamaswap":{"aliases":["qwen3.8-27b:low","qwen3.8-27b:off","qwen3.8-27b:xhigh"],"type":"model"}},"status":{"value":"loaded"}}],"object":"list"}
http_code=200 time_total=0.052891
```
**Reachable, fast (53ms).** Server identifies as `llama-swap` (a router/manager for llama.cpp-family `llama-server` instances) serving `gemma4-26b-a4b` (gguf) and `qwen3.8-27b`. This directly refutes H2 (network-unreachable) as the current condition — the box is healthy and responsive right now, contrary to the stale pre-session MEMORY.md note about a "No route to host" condition.

## Step 4: Baseline probe (current grader.py suppression fields)

```
$ curl ... -d '{"model":"gemma4-26b-a4b","messages":[...],"max_tokens":16,"temperature":0.1,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
{"choices":[{"finish_reason":"stop","index":0,"message":{"role":"assistant","content":"ok"}}],...,"usage":{"completion_tokens":2,...}}
HTTP:200 TIME:0.491907
```
**No thinking-presence signal.** `finish_reason:"stop"`, clean 2-token `"ok"` content, no `reasoning_content` field, no `<think>` tags. 0.49s latency. **The current suppression fields work correctly on this box right now.**

Per the plan, since Step 4 showed no thinking-presence signal, Step 5's Candidates A-E (alternate suppression fields) were **not run** — there is nothing to find an alternative for.

## Step 5: No-fields-at-all comparison baseline

```
$ curl ... -d '{"model":"gemma4-26b-a4b","messages":[...],"max_tokens":16,"temperature":0.1}'
{"choices":[{"finish_reason":"length","index":0,"message":{"role":"assistant","content":"","reasoning_content":"The user wants a reply consisting of exactly one word: \"ok"}}],...}
HTTP:200 TIME:1.491086
```
**Without any suppression fields, thinking is confirmed ON by default** on this box/model: `reasoning_content` field present (visible chain-of-thought), `finish_reason:"length"` with **empty** `content` (the 16-token budget was entirely consumed by hidden reasoning before any visible answer). This proves the underlying failure mode the operator described is real and reproducible on this box — but only when the suppression fields are *absent*, which they are not in the shipped code.

## Step 6: Grading-shaped probes (real GRADE_SCHEMA payloads)

**Suppressed variant (current fields), objective 1 (correct `add` implementation):** grade `A`, `approved:true`, correct feedback, `finish_reason:"stop"`, clean JSON. **4.14s** (prompt+44 completion tokens; per-token generation ~80ms, i.e. general GPU load, not thinking).

**Suppressed variant, objective 2 (buggy `is_even`, `== 1` instead of `== 0`):** grade `F`, `approved:false`, feedback correctly identifies the off-by-one logic bug, `finish_reason:"stop"`, clean JSON. **6.61s**.

**Thinking-enabled reference, objective 1:** `finish_reason:"length"`, `content:""` — **no verdict produced at all**, the entire 256-token budget consumed by reasoning (visible in `reasoning_content`, which itself trails off mid-thought without ever reaching an answer). **19.36s.** This response would fail `grader.py`'s `if not result_text` check → `infrastructure_error: "Ollama returned empty response"`.

**Thinking-enabled reference, objective 2:** `finish_reason:"length"`, `content` is **truncated/incomplete JSON** (`{"grade": "F", "approved": false, "feedback": "The function returns True if` — cut off mid-string). **18.91s.** This would fail `grader.py`'s `json.loads` → `infrastructure_error: "Non-JSON response"`.

**This is strong evidence for the class of failure the operator is worried about**: when thinking is NOT suppressed, grading on this box takes ~19s per call (well into the range that could plausibly extend to the operator's reported 22-48s under heavier concurrent load) and frequently produces empty or malformed output rather than a usable verdict. But this failure mode requires the suppression fields to be *absent* — and in the currently-running v1.1.10 code, they are sent by default (`grader.py:214-217`, `spots.grader.thinking` unset in the deployed config → `spot_thinking=False` → fields sent).

## Finding

**INCONCLUSIVE — specifically the "amd-halo reachable and current fields already suppress thinking, no fix needed" branch.** Neither H1 nor H2 is confirmed:
- Not H1: the current suppression fields (`reasoning_effort:"none"` + `chat_template_kwargs.enable_thinking:false`) fully suppress thinking on this box for this model, right now — clean, fast (0.49s for a trivial call, 4-7s for realistic grading calls), correct verdicts. No alternate field combination needed to be found because the current one already works.
- Not H2: amd-halo is reachable, fast, and healthy (53ms `/v1/models`, 0.2-6.6s chat completions) — not network-unreachable.

**Per Task 2's branch logic, no code change is warranted.** The failure mode the operator described is real and was reproduced live in this task (Step 5, Step 6 thinking-enabled reference) — but only when the suppression fields are absent, and the shipped `grader.py` (confirmed live and running, matching this worktree's v1.1.10 HEAD) already sends them by default. Whatever the operator observed causing the reported circuit-breaker trips likely predates this default (pre-v1.1.9), came from a config with `spots.grader.thinking:true` set (which would have deliberately omitted the fields — the "strict-endpoint" escape hatch), or came from a different box/session than what this investigation could reach. This finding does not identify what that was; it only establishes that no defect currently exists in the live code to fix.
