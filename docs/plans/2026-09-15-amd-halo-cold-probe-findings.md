# AMD Halo Grader Cold Probe — Findings

> **Recorded:** 2026-09-15
> Evidence source: live `curl` command run directly in this session.

## Deployed grader model (Step 1)

```
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

No `spots.grader` override — grader spot resolves to `openai.model` = `gemma4-26b-a4b`, matching the value assumed by the plan.

## Probe result (Step 2)

Command:
```
curl -sS -m 30 -w "\nHTTP:%{http_code} TIME:%{time_total}\n" -X POST http://100.76.144.47:8080/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer ollama" -d '{"model":"gemma4-26b-a4b","messages":[{"role":"user","content":"Reply with exactly one word: ok"}],"max_tokens":16,"temperature":0.1,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```

Raw output:
```
{"choices":[{"finish_reason":"stop","index":0,"message":{"role":"assistant","content":"ok"}}],"created":1789523068,"model":"gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf","system_fingerprint":"b9413-Debian","object":"chat.completion","usage":{"completion_tokens":2,"prompt_tokens":20,"total_tokens":22,"prompt_tokens_details":{"cached_tokens":4}},"id":"chatcmpl-H3AHc72LKUJW1KgZJtjWPeo0q0hWoYPv","timings":{"cache_n":4,"prompt_n":16,"prompt_ms":284.66,"prompt_per_token_ms":17.79125,"prompt_per_second":56.20740532565165,"predicted_n":2,"predicted_ms":31.226,"predicted_per_token_ms":15.613,"predicted_per_second":64.04918977774932}}
HTTP:200 TIME:0.581117
```

## Verdict (Step 3)

**PASS** — all four conditions held:
- HTTP status: `200`
- `choices[0].finish_reason`: `"stop"`
- `choices[0].message`: no `reasoning_content` field present
- `TIME`: `0.581117` ≤ 15.0

## Conclusion

Hypothesis disproved. `grader.py`'s default thinking-suppression fields (`grader.py:214-217`: `reasoning_effort:"none"` + `chat_template_kwargs:{"enable_thinking":false}`) work correctly against AMD Halo as of 2026-09-15 — response in 0.58s, clean `"ok"` content, no thinking leakage. No 30s timeout exists in the grader call path. No code change needed. Consistent with commit `3aa719b` (2026-09-13).
