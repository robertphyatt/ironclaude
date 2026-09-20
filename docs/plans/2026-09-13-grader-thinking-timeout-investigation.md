# Grader Circuit-Breaker Trip Against amd-halo Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Reproduce the real cause of the grader circuit breaker tripping against amd-halo with live evidence, then implement only the branch that evidence confirms.

**Requirements:** `docs/plans/2026-09-13-grader-thinking-timeout-investigation-requirements.md`

**Design:** `docs/plans/2026-09-13-grader-thinking-timeout-investigation-design.md`

**Architecture:** Two-task, dependency-ordered plan. Task 1 is a read-only/network diagnostic (no source files touched, except its own findings record) that determines which hypothesis is true: H1 (amd-halo's server ignores the current disable-thinking fields, causing slow responses that trip some timeout) or H2 (amd-halo has a genuine network-reachability problem, and the breaker is behaving correctly). Task 2 depends on Task 1 and its content branches on Task 1's finding — only the confirmed branch is implemented.

**Tech Stack:** Python (Commander daemon), `requests`-based HTTP clients, `pytest`.

**Execution invariants (apply to every step below):** Shell state does not persist between steps — use literal absolute paths, not variables from a prior step. Quote any glob. No `2>/dev/null` on evidence-gathering commands — a failure must be visible, not silently swallowed. Do not assume a particular curl/ps outcome; record what actually happens. This session runs inside a managed git worktree — a command that explicitly names `/Users/roberthyatt/Code/ironclaude` (the primary checkout) as a path argument is blocked by `professional-mode-guard.sh`'s managed-worktree enforcement, even for reads; use `git worktree list --porcelain` (no path argument) to learn the primary checkout's location and HEAD instead.

**Revision note (after tier-up plan review):** A blind same-tier review found this plan HAS-ISSUES on 3 Critical findings; a tier-up fix advisor confirmed all 3 (no design-level retreat needed). This version fixes: (1) Task 1 Step 2's command, which would have been blocked by managed-worktree enforcement; (2) Task 1's thinking-suppression probe, which previously tested only 2 variants and could never produce the "confirmed-working field" Task 2's H1 branch requires; (3) the verdict-equivalence test, which previously could not fail if the fix were reverted.

---

## Task 1: Live root-cause investigation

**Files:**
- Create: `docs/plans/2026-09-13-grader-thinking-timeout-investigation-findings.md` (the only file this task writes — a record of every step's raw output and the finding)

No tests required: this task makes no source-code change; it produces a finding that gates Task 2.

**Step 1: Identify the live Commander daemon process**

Run:
```bash
ps aux | grep "ironclaude.main" | grep -v grep
```
Record the PID, start time, and the full command line (including the python interpreter path — an editable install means the interpreter path identifies which checkout's code is actually loaded, per `commander/Makefile:16`: `pip install -qe .` then `$(PYTHON) -u -m ironclaude.main`).

**Step 2: Identify the primary checkout and its HEAD, without naming its path as a command argument**

Run:
```bash
git worktree list --porcelain
```
This lists every worktree including the primary checkout, without any command-line argument naming a path outside this managed worktree (naming one directly, e.g. `git -C /Users/roberthyatt/Code/ironclaude ...`, is blocked by the managed-worktree enforcement hook even for reads). The primary checkout is the first stanza (no `.ironclaude/worktrees/` in its `worktree` line); record its `worktree` path and `HEAD` sha. Compare that sha against this worktree's `255d594` and against Step 1's process command line (does the running interpreter's path match the primary checkout's `.venv`?) — record whatever you actually observe, do not assume they match.

**Step 3: Probe amd-halo reachability and identify the server implementation**

Run:
```bash
curl -sS -m 5 -w "\nhttp_code=%{http_code} time_total=%{time_total}\n" http://amd-halo:8080/v1/models
```
Record the exit code, `http_code`, `time_total`, and the **full response body** (not discarded) — inspect it for a server-identifying signature (e.g. `"object":"list"` with model IDs typical of vLLM/llama.cpp/llama-server implementations). This identifies which candidate fields in Step 5 are plausible before you run them. A nonzero curl exit code with a connection-refused/no-route message supports H2; a 2xx `http_code` with `time_total` well under 5s supports "reachable, probe mechanism itself works."

**Step 4: If Step 3 shows amd-halo reachable, run the baseline (current-fields) probe and record explicit thinking-presence signals**

Run (mirrors the exact payload `grader.py:214-217` sends today):
```bash
curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ollama" \
  -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly the word: ok"}],"max_tokens":16,"temperature":0.1,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```
Record the full response body and latency, and check for each of these three thinking-presence signals explicitly (any one present = thinking is still happening):
1. A `reasoning_content` or `reasoning` field anywhere in the response (a separate channel `openai_client.py`'s `_read_content` never reads, so it would cost latency invisibly to the grader).
2. `finish_reason` is `"length"` with empty or near-empty `message.content` (the 16-token budget was consumed by hidden reasoning before any visible answer).
3. `<think>...</think>` tags inside `message.content` (visible, and already stripped by `grader.py:245` post-hoc, but its presence here means the field did not suppress it).

**Step 5: If Step 3 shows amd-halo reachable, probe each candidate suppression mechanism**

Only run this step if Step 4 showed at least one thinking-presence signal (i.e. the current fields did not work). Run each candidate below (skip any candidate that's clearly inapplicable to the server family identified in Step 3, but if the server family is undetermined, run all of them), recording the same three signals plus latency for each:

- **Candidate A** (`chat_template_kwargs` alone, no `reasoning_effort`):
  ```bash
  curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
    -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
    -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly the word: ok"}],"max_tokens":16,"temperature":0.1,"chat_template_kwargs":{"enable_thinking":false}}'
  ```
- **Candidate B** (`reasoning_effort` alone, no `chat_template_kwargs`):
  ```bash
  curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
    -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
    -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly the word: ok"}],"max_tokens":16,"temperature":0.1,"reasoning_effort":"none"}'
  ```
- **Candidate C** (top-level `enable_thinking`, not nested):
  ```bash
  curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
    -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
    -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly the word: ok"}],"max_tokens":16,"temperature":0.1,"enable_thinking":false}'
  ```
- **Candidate D** (`thinking` object, Anthropic-style shape — worth testing regardless of how it was suggested, since this step tests it empirically rather than trusting any source):
  ```bash
  curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
    -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
    -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly the word: ok"}],"max_tokens":16,"temperature":0.1,"thinking":{"type":"disabled"}}'
  ```
- **Candidate E** (system-message instruction, works below API-level control on many chat models):
  ```bash
  curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
    -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
    -d '{"model":"gemma4-26b-a4b","messages":[{"role":"system","content":"Respond directly without extended reasoning or thinking."},{"role":"user","content":"Reply with exactly the word: ok"}],"max_tokens":16,"temperature":0.1}'
  ```

Also run the no-fields-at-all baseline for comparison against all of the above:
```bash
curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
  -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
  -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly the word: ok"}],"max_tokens":16,"temperature":0.1}'
```

**Step 6: If Step 4 (or a Step 5 candidate) showed NO thinking-presence signal, capture real grading-shaped evidence**

Run this for (a) the confirmed-working variant (current fields if Step 4 already worked, or whichever Step 5 candidate worked) and (b) the thinking-enabled reference (the no-fields-at-all baseline from Step 5), using the actual `GRADE_SCHEMA` shape `grader.py` sends (`response_format: json_schema`) and two canned grading objectives — this is the real requirement-3 evidence (Task 2's verdict-equivalence test and any timeout sizing must be derived from these captures, not from the trivial "ok" probe):

Objective 1 payload (suppressed variant — substitute whichever fields Step 4/5 confirmed work in place of the ones shown):
```bash
curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
  -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
  -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"You are grading whether a response correctly implements the requested feature. Respond only with JSON matching the schema.\n\nTask: Add a function add(a, b) that returns a+b. Response: def add(a, b): return a + b. Grade this response."}],"max_tokens":256,"temperature":0.1,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false},"response_format":{"type":"json_schema","json_schema":{"name":"verdict","schema":{"type":"object","properties":{"grade":{"type":"string","enum":["A","B","C","D","F"]},"approved":{"type":"boolean"},"feedback":{"type":"string"}},"required":["grade","approved","feedback"]}}}}'
```
Objective 2 payload (same suppressed variant, different task):
```bash
curl -sS -m 60 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" http://amd-halo:8080/v1/chat/completions \
  -H "Content-Type: application/json" -H "Authorization: Bearer ollama" \
  -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"You are grading whether a response correctly implements the requested feature. Respond only with JSON matching the schema.\n\nTask: Write a function is_even(n) that returns True if n is even. Response: def is_even(n): return n % 2 == 1. Grade this response."}],"max_tokens":256,"temperature":0.1,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false},"response_format":{"type":"json_schema","json_schema":{"name":"verdict","schema":{"type":"object","properties":{"grade":{"type":"string","enum":["A","B","C","D","F"]},"approved":{"type":"boolean"},"feedback":{"type":"string"}},"required":["grade","approved","feedback"]}}}}'
```
Then re-run both objectives with NO suppression fields (thinking-enabled reference) for direct comparison. Record all four full JSON bodies, latencies, and the parsed `grade`/`approved`/`feedback` for each.

**Step 7: If Step 3 shows amd-halo unreachable, capture the exact error**

Run:
```bash
curl -sS -m 5 -v http://amd-halo:8080/v1/models
```
Record the exact connection-stage error (DNS failure vs. connection refused vs. timeout vs. route unreachable) and its wording — this is the evidence for H2 if confirmed.

**Step 8: Record the finding**

Write to `docs/plans/2026-09-13-grader-thinking-timeout-investigation-findings.md` the raw output of every step above and an explicit finding, based only on that output (not on `/tmp/ic/daemon.log` narration or any episodic-memory archive content — both are excluded as evidence per the design doc):
- **H1 confirmed**: amd-halo reachable, current fields (Step 4) showed a thinking-presence signal, AND at least one Step 5 candidate showed NO thinking-presence signal (a working suppression mechanism was found).
- **H2 confirmed**: amd-halo unreachable (Step 3/7).
- **Inconclusive**: amd-halo reachable and current fields already suppress thinking (no fix needed — this is a "problem doesn't reproduce" outcome, not H1), OR current fields fail AND no Step 5 candidate works either (thinking cannot be suppressed by any tested mechanism). In either inconclusive case, record the actual observation and stop; Task 2 becomes a no-op report rather than a fix.

**Step 9: Stage the findings file**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c add -f docs/plans/2026-09-13-grader-thinking-timeout-investigation-findings.md
```
Expected: findings file staged.

---

## Task 2: Apply the fix confirmed by Task 1

**Depends on:** Task 1

**Files:**
- Modify: `commander/src/ironclaude/grader.py:214-217` (only if H1)
- Modify: `commander/src/ironclaude/backend_resolver.py` (only if H1 and Task 1's measured latency shows a timeout genuinely needs adjusting)
- Modify: `commander/tests/test_local_grader.py` (only if H1)
- Modify: `CHANGELOG.md` (only if H1)

**Branch on Task 1's Step 8 finding:**

### If H2 confirmed or inconclusive

No code change. No tests required: no executable code is touched. Skip directly to reporting: summarize Task 1's findings back to the operator in your task completion notes. Do not write to memory claiming resolution (per requirements item 5) — this is a diagnostic result, not a fix, until the operator decides the next step.

### If H1 confirmed

**Step 1 (RED): Update the existing suppression-field test to the confirmed field(s)**

In `commander/tests/test_local_grader.py`, modify `test_grade_openai_default_thinking_off_injects_suppression` (currently at `:876-891`) so its assertions check for the field(s)/value(s) that Task 1 Step 5 actually demonstrated suppress thinking — not `reasoning_effort`/`chat_template_kwargs.enable_thinking` if those were shown not to work. Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c/commander && .venv/bin/python -m pytest tests/test_local_grader.py -k test_grade_openai_default_thinking_off_injects_suppression -v
```
Expected: FAIL (test now asserts a field the current implementation doesn't send yet).

**Step 2 (GREEN): Implement the confirmed suppression mechanism**

Edit `commander/src/ironclaude/grader.py:214-217` (currently `payload["reasoning_effort"] = "none"; payload["chat_template_kwargs"] = {"enable_thinking": False}`) to send the field(s) Task 1 confirmed work, still gated behind the existing `if not spot_thinking:` condition so `spots.grader.thinking:true` continues to omit both fields for strict endpoints (per `test_grade_openai_thinking_true_omits_suppression`, `:894-916`, which must continue passing unmodified). Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c/commander && .venv/bin/python -m pytest tests/test_local_grader.py -k "test_grade_openai_default_thinking_off_injects_suppression or test_grade_openai_thinking_true_omits_suppression or test_grade_openai_thinking_off_preserves_core_payload_fields" -v
```
Expected: 3 passed.

**Step 3: Size the timeout, only if Task 1's grading-shaped probe (Step 6) shows one is genuinely needed**

Using the latency measured by Task 1 Step 6's grading-shaped probe (not the trivial 16-token "ok" probe, which cannot reproduce realistic inference latency) — if the confirmed-working suppressed variant's latency is still long enough to matter for breaker classification, adjust `probe_timeout`/`connect_timeout` resolution in `commander/src/ironclaude/backend_resolver.py` (never `fast_lane_grade_timeout_seconds` in `main.py:2691-2702`, which bounds an unrelated operator-fast-lane path per requirements item 4) using that exact measured value, not a round guess. If the measured latency is already well under the existing 3s `probe_timeout`/600s read `timeout`, skip this step — no change needed.

**Step 4 (RED): Write the verdict-equivalence test, tied to Task 1's actual captured evidence**

Add a new test to `commander/tests/test_local_grader.py`, following the existing `_openai_post_response` helper pattern (`:726-730`) used by the tests above. Use the exact two grading-shaped response bodies Task 1 Step 6 captured (suppressed variant vs. thinking-enabled reference, for one of the two canned objectives) as the mocked `requests.post` return values verbatim — not author-chosen placeholder content. The test must assert BOTH:
1. `grade()` against the suppressed-variant mock and `grade()` against the thinking-enabled-reference mock parse to equal verdict dicts (`grade`/`approved`/`feedback`).
2. The request sent on the suppressed path (`mock_post.call_args[1]["json"]`) contains the confirmed field(s) from Step 2 — the same style of assertion as `test_grade_openai_default_thinking_off_injects_suppression` (`:889-891`).

Assertion 2 is what makes this test fail if the fix is reverted — assertion 1 alone cannot, since both mock bodies are fixed regardless of what `grader.py` sends. Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c/commander && .venv/bin/python -m pytest tests/test_local_grader.py -k test_grade_verdict_equivalence -v
```
Expected: FAIL (test doesn't exist yet).

**Step 5 (GREEN): Confirm the verdict-equivalence test passes**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c/commander && .venv/bin/python -m pytest tests/test_local_grader.py -v
```
Expected: all tests in the file pass, including the new verdict-equivalence test.

**Step 6: Full suite regression check**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c/commander && .venv/bin/python -m pytest tests/ -v
```
Expected: no new failures relative to the pre-change baseline.

**Step 7: Update CHANGELOG.md**

Add an `## [Unreleased]` section at the top of `CHANGELOG.md` (below the versioning note, above `## 1.1.10`) describing the confirmed root cause and fix, following this repo's existing entry style (see the `## 1.1.10` section for format).

**Step 8: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/352afa8c-9e57-42c1-bada-de222093de5c add commander/src/ironclaude/grader.py commander/src/ironclaude/backend_resolver.py commander/tests/test_local_grader.py CHANGELOG.md
```
(`git add` on an untouched `backend_resolver.py` is harmless if Step 3 was skipped.) Expected: changes staged (professional mode blocks commit).
