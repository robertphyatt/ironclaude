# AMD Halo Grader Cold Probe Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Run one live timed probe against AMD Halo reproducing `grader.py`'s actual request shape, and record whether the prior finding (thinking already disabled by default, no 30s timeout, box healthy) still holds. No code changes.

**Requirements:** `docs/plans/2026-09-15-amd-halo-cold-probe-requirements.md`

**Design:** `docs/plans/2026-09-15-amd-halo-cold-probe-design.md`

**Architecture:** A single diagnostic task: confirm the deployed grader model, issue one `curl` POST to AMD Halo's `/v1/chat/completions` using `grader.py`'s exact default suppression fields, and write a findings doc recording pass/fail against the ≤15s / no-`reasoning_content` condition.

**Tech Stack:** curl, bash.

**Execution invariants for every step below:**
- Shell state does not persist between steps — each command is self-contained with literal absolute paths.
- Bash cwd during execution is `commander/`, not the repo root — git commands use `git -C /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/4ffd1c68-a4a6-4b67-8322-39a5677c6c87`.
- `docs/` is gitignored — staging the findings doc requires `git add -f`.

---

## Task 1: Run the cold probe and record findings

**Files:**
- Create: `docs/plans/2026-09-15-amd-halo-cold-probe-findings.md`

No tests required: this is a diagnostic probe producing a findings document, not executable code.

**Step 1: Confirm the current deployed grader model**

Run:
```bash
cat /Users/roberthyatt/.claude/ironclaude-hooks-config.json
```

Expected (verified 2026-09-15, re-confirm it has not changed):
```json
{
  "backend": "openai",
  "openai": {
    "base_url": "http://100.76.144.47:8080/v1",
    "model": "gemma4-26b-a4b",
    "max_tokens": 1024
  },
  "spots": {
    "shadow": {
      "model": "qwen3.8-27b"
    }
  },
  "timeout_seconds": 600
}
```

No `spots.grader` override exists, so the grader spot resolves to `openai.model` = `gemma4-26b-a4b` (per `commander/src/ironclaude/backend_resolver.py:65`, `resolve_backend`). If this file's `openai.model` or a new `spots.grader.model` differs from `gemma4-26b-a4b`, use the actual current value as `<MODEL>` in Step 2 instead.

**Step 2: Issue the probe request**

Run (using `<MODEL>` = `gemma4-26b-a4b` per Step 1, or the actual current value if it differed):
```bash
curl -sS -m 30 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" \
  -X POST http://100.76.144.47:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ollama" \
  -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly one word: ok"}],"max_tokens":16,"temperature":0.1,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```

This mirrors `commander/src/ironclaude/grader.py:199-217`'s exact payload fields for the OpenAI backend (`reasoning_effort`, `chat_template_kwargs`, `max_tokens`, `temperature`) and `commander/src/ironclaude/openai_client.py:133-134`'s `Authorization: Bearer ollama` header.

Record the full raw output (JSON body plus the `HTTP:`/`TIME:` trailer) verbatim — this is the evidence for the findings doc.

**Step 3: Evaluate pass/fail**

Pass condition (all three, per `docs/plans/2026-09-15-amd-halo-cold-probe-requirements.md` requirement 2):
- HTTP status `200`
- Response JSON's `choices[0].finish_reason` is `"stop"`
- Response JSON's `choices[0].message` has no `reasoning_content` field
- `TIME:` value ≤ 15.0

If the curl command itself fails to connect or hits the 30s `-m` cap: that is a fail — record it as "box unreachable/non-responsive at probe time," not a code defect.

**Step 4: Write the findings doc**

Create `/Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/4ffd1c68-a4a6-4b67-8322-39a5677c6c87/docs/plans/2026-09-15-amd-halo-cold-probe-findings.md` with this structure, filling in the actual Step 1 config output, Step 2 raw curl output, and Step 3 verdict:

```markdown
# AMD Halo Grader Cold Probe — Findings

> **Recorded:** 2026-09-15
> Evidence source: live `curl` command run directly in this session.

## Deployed grader model (Step 1)

[paste actual cat output]

## Probe result (Step 2)

[paste actual curl output verbatim, including HTTP:/TIME: trailer]

## Verdict (Step 3)

[PASS or FAIL] — [state which of the four pass conditions held/failed, with the actual observed values]

## Conclusion

[If PASS: "Hypothesis disproved. grader.py's default thinking-suppression fields (grader.py:214-217) work correctly against AMD Halo as of 2026-09-15. No 30s timeout exists in the grader call path. No code change needed. Consistent with commit 3aa719b (2026-09-13)."]

[If FAIL: state exactly which condition failed and the observed value — this contradicts 3aa719b's finding and requires a follow-up brainstorm before any code change. Do not propose a fix in this document.]
```

**Step 5: Stage the findings doc**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude/.ironclaude/worktrees/4ffd1c68-a4a6-4b67-8322-39a5677c6c87 add -f docs/plans/2026-09-15-amd-halo-cold-probe-findings.md
```

Expected: Findings doc staged (professional mode blocks commit).
