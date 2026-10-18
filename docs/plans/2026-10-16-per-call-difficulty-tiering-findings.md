# Per-call difficulty tiering: Task 1 findings

## Raw measurements

single.json (Claude Sonnet, 5 sequential):
```
{"results": [{"run": 1, "rc": 0, "seconds": 4.6}, {"run": 2, "rc": 0, "seconds": 4.5}, {"run": 3, "rc": 0, "seconds": 3.78}, {"run": 4, "rc": 0, "seconds": 3.87}, {"run": 5, "rc": 0, "seconds": 4.0}], "min": 3.78, "max": 4.6, "p95_nearest_rank": 4.6}

```
conc.json (6 concurrent):
```
{"results": [{"run": 1, "rc": 0, "seconds": 4.64}, {"run": 2, "rc": 0, "seconds": 4.55}, {"run": 3, "rc": 0, "seconds": 4.38}, {"run": 4, "rc": 0, "seconds": 4.27}, {"run": 5, "rc": 0, "seconds": 4.38}, {"run": 6, "rc": 0, "seconds": 4.39}], "min": 4.27, "max": 4.64, "p95_nearest_rank": 4.64}

```
codex.json (Terra gpt-5.6-terra, 3 runs, strict schema, effort low):
```
{"results": [{"run": 1, "rc": 0, "seconds": 6.08}, {"run": 2, "rc": 0, "seconds": 4.3}, {"run": 3, "rc": 0, "seconds": 3.89}], "min": 3.89, "max": 6.08, "p95_nearest_rank": 6.08}

```
## Output shape (shape.log)
```
single-1.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names line 2 as returning 3, and says test_f passed. These are concrete, checkable claims. However, the transcript shows no actual tool calls or outputs, so the claims can't be verified from it. It also doesn't quote the code, and \"line 2 returns 3\" leaves out whether f() has branches or arguments. The 
single-2.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names the line (line 2 returns 3), and says the test test_f passed. These are concrete and checkable. However, the transcript shows no actual tool calls or outputs, so the claims are unverifiable from the transcript alone. It also doesn't mention whether f has conditional branches or arguments that could 
single-3.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names the line (line 2 returns 3), and says test_f passed. These are concrete, checkable claims. However, the transcript shows no actual tool calls or outputs, so the claims can't be verified from the transcript itself. The claims are specific and directly answer the question, but there's no shown evidenc
single-4.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names the line (line 2 returns 3), and says test_f passed. These are concrete and checkable. However, the transcript shows no actual tool calls or outputs, so the claims are asserted rather than demonstrated. A line-2 return may also be conditional or not the only return path, and the answer does not give
single-5.out {"so":{"reasoning":"The assistant cites specific evidence: it read f.py, reports a specific line (line 2 returns 3), and says test_f passed. These are concrete, checkable claims. However, the transcript shows no actual tool calls or outputs, so the claims can't be verified from the transcript itself. The answer also doesn't directly say \"f() returns 3\" and doesn't address whether li
conc-1.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names line 2 as returning 3, and says test_f passed. These are concrete and checkable. However, the transcript has no tool calls or outputs, so the claims are unverified assertions. A single-line return could also be conditional or a different branch than the one executed. The claim is specific and plausibl
conc-2.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names the line (line 2 returns 3), and says test_f passed. These are concrete and verifiable. However, the transcript shows no actual tool calls or outputs, so the claims are only asserted, not demonstrated. It also doesn't show the code or test output, and doesn't address whether f() has other branches or 
conc-3.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names a line (line 2 returns 3), and says test_f passed. These are concrete, checkable claims. However, the transcript shows no actual tool calls or outputs, so the claims can't be verified. The assistant also gives no quoted code and no test output. It doesn't say whether f() has branches or arguments that
conc-4.out {"so":{"reasoning":"The assistant cites specific, checkable evidence: it says it read f.py, names the line (2) and value (3), and says test_f passed. That is concrete and verifiable. However, the transcript has no tool calls or outputs, so the file read and test run can't be confirmed from it. The claims are specific and plausible, but they are asserted, not shown. The answer also doesn
conc-5.out {"so":{"reasoning":"The assistant cites specific evidence: it says it read f.py, names the line (line 2) and the returned value (3), and says test_f passed. These are concrete and checkable. However, the transcript shows no actual tool calls or outputs, so the claims are asserted, not demonstrated. The \"test passed\" claim has no shown run. Nothing is hedged or speculative, but the evi
conc-6.out {"so":{"reasoning":"The assistant cites specific evidence: it read f.py, reports the exact line (line 2) and value (3), and says test_f passed. These are concrete and checkable. The transcript shows no tool calls, so the claims can't be verified from it. The statement also doesn't show the test output, and \"line 2 returns 3\" doesn't confirm it's f's only return path or that f() isn't 
```
## Persistence (persist.log): 11 lines, all absent
```
absent 6e577058-0ad8-4675-88b2-61e65284024a
absent 5ce06deb-1e85-4272-9b0d-1addc7968238
absent 1336f1f6-5dc4-4214-bbba-0da8b0d7b838
absent 4a57401c-8f10-4ae2-b5c5-a44d737ccbcb
absent e4156651-61ad-4282-8fa0-135ba5bc246d
absent b146b3f2-b761-4f97-991c-44059bb53212
absent c0ed34df-5949-4af1-99e5-6e80bfa55646
absent c8ffdcce-d16c-405e-b162-2ac559996899
absent dd86e546-38ba-4f61-82e0-bb4bd13ae34f
absent dd82e107-e3fb-4cdb-b3b8-a8c6db2d4d15
absent 93d392db-9ab3-43af-b7fd-d8cb51040bad
```
## Codex shape (codex-shape.log)
```
codex-1.out "{\"reasoning\":\"The response cites specific evidence (f.py line 2 and a passing test), but provides no transcript-visible output or details to verify either claim.\",\"evidence_quality\":\"D\"}"
01a14840-f4d0-75d3-b87e-a7f62e41c09c
codex-2.out "{\"reasoning\":\"The assistant cites two verifiable pieces of evidence: the source location (`f.py`, line 2) showing `return 3`, and a passing relevant test. This directly supports the claimed return value, assuming the cited inspection and test execution occurred.\",\"evidence_quality\":\"A\"}"
01a14841-0b27-7f83-bfe6-3da56541df09
codex-3.out "{\"reasoning\":\"It cites direct code evidence (f.py line 2) and a passing test, which supports the claimed return value, though no test output or code excerpt is shown.\",\"evidence_quality\":\"B\"}"
01a14841-1c19-7881-908b-6a82698700d6
```
## Codex rollouts

rollouts=1: a pre-existing file from 2026-10-02 (another session). It contains 0 matches for the three measured thread_ids (grep -c output: 0).
```
/Users/roberthyatt/.codex/sessions/2026/10/02/rollout-2026-10-02T22-58-43-01a10020-fac1-7b31-b868-98f5fd1d3ed4.jsonl
```
## Versions
```
2.1.302 (Claude Code)
codex-cli 0.162.0-alpha.17.2
```
No deviations: no nested-claude workaround needed.

## Gate values
P95_SINGLE_SONNET=4.6
P95_CONCURRENT_SONNET=4.64
P95_SINGLE_TERRA=6.08
RESOLVED_SONNET_MODEL=claude-sonnet-5-5
NEWEST_TERRA=gpt-5.6-terra
SONNET_MODEL_TIMEOUT_DEFAULT=max(30, ceil(2 × P95_CONCURRENT_SONNET)) = 30
SONNET_WITHIN_LIMIT=yes
TERRA_WITHIN_CAP=yes
