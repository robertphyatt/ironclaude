# llama.cpp json_schema `maxLength` Ceiling — Bisect Findings

> **Date:** 2026-09-26
> **Plan:** docs/plans/2026-09-26-v1-1-13-grammar-cap-and-idempotent-completion.md (Task 1)

## Server and models

- **Endpoint:** `http://100.76.144.47:8080/v1`. This is the `openai.base_url` in `~/.claude/ironclaude-hooks-config.json`, served by llama.cpp build b9413 on amd-halo.
- **Grader spot model:** `gemma4-26b-a4b`, resolved with `resolve_backend(cfg, 'grader').model`.
- **Shadow spot model:** `qwen3.8-27b`, resolved with `resolve_backend(cfg, 'shadow').model`.

## Method

The script inlined the five real grade schemas as they stand at HEAD `60509a1`:
- `_PROMPT_WAITING_SCHEMA`;
- `_LOCAL_VERDICT_SCHEMA`, `_LOCAL_CONFIDENCE_SCHEMA` and `_LOCAL_HEALTH_SCHEMA`;
- shadow `GRADER_VERDICT_SCHEMA`.

For each N, every long field was set to `maxLength` N:
- `interaction_block`, `question` and `authority_text` in `_PROMPT_WAITING_SCHEMA`;
- `feedback` in the verdict, confidence and shadow schemas;
- `diagnosis` in the health schema.

Each schema received a realistic prompt; the prompt-waiting prompt used the real `_PROMPT_WAITING_SYSTEM` plus a long terminal tail. Each was sent 3 times, 15 requests per N in total, with `max_tokens` 8192, temperature 0.1 and thinking disabled, through `response_format: json_schema`. An N passes only if all 15 requests return HTTP 200.

The sequence was:
1. Controls: 1024 must pass and 2048 must fail.
2. A 64-step bisect between the two.
3. A recheck of the highest passing N.

That bisect's cap rule was `max(1024, HIGHEST_PASS - 64)` (superseded; see Exact ceiling).

- **Script:** `scratchpad/bisect_maxlength.py`
- **Log:** `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect-maxlength.log`

## Results

| N | Result |
|---|---|
| 1024 | PASS (control) |
| 2048 | FAIL (control): HTTP 500 on all 15 requests, every schema, both models |
| 1536 | PASS |
| 1792 | PASS |
| 1920 | PASS |
| 1984 | PASS |

Result line (`bisect-result.txt`): `HIGHEST_PASS=1984 FIRST_FAIL=2048 CAP=1920`

- **Recheck caveat:** the recheck of 1984 was satisfied from the log (the script's resume path) rather than a second live probe. The 1984 PASS therefore rests on one round of 15/15 requests.
- **Unprobed range:** N between 1985 and 2047 was not probed in this bisect; the exact search below covers 1985–2000.
- **Superseded:** the one-step margin rule (cap 1920) was replaced by the exact search and operator decision below.
- **Both models agree:** the 2048 failure appears for both models, so the limit is in the server's grammar builder, not in either model.

## Exact ceiling (follow-up)

- **N = 2000:** 14 requests (all five schemas; the shadow schema's runs 0–1), every one HTTP 500, on both models. The probe was stopped after 14 because the result was conclusive. Log: `scratchpad/boundary-2000.log`.
- **Binary search between 1984 (pass) and 2000 (fail):** each N stops at its first non-200 response. 1992, 1996, 1998 and 1999 each returned 200 on all 15 requests. Result: `EXACT_HIGHEST_PASS=1999 EXACT_FIRST_FAIL=2000`. Log: `scratchpad/pin-max.log`.
- **Upstream cause (reported by the amd-halo box):** llama.cpp hardcodes `MAX_REPETITION_THRESHOLD 2000` in `llama-grammar.cpp`. It is the stock default, unchanged on current master, with no runtime flag. A json_schema `maxLength` compiles to a `char{0,N}` rule, so every stock llama.cpp deployment has this limit. The measured rule is exactly N < 2000.
- **Cap:** the operator chose `GRAMMAR_MAX_STRING_LENGTH = LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1` = 1999, with no extra margin.

## Prompt-detection consequence

`validate_prompt_candidate` (`tmux_manager.py:203-205`) accepts an `interaction_block` of up to 4096 characters and a question or authority text of up to 2048. With the grammar cap, the grader can return at most `GRAMMAR_MAX_STRING_LENGTH` (1999) characters for these fields.

A worker's final interaction block longer than 1999 characters can't be copied whole. If the model copies a long block, the grammar cuts it off, and the validator's suffix check (`_suffix_is_prompt_chrome`, `tmux_manager.py:223`) rejects it: the text after a cut-off block is the rest of that block, not prompt chrome.

The validator does not need the whole block, though. It needs a final contiguous block that contains the question and its options and is followed only by chrome. A later live probe (run `20260927T212748`, 2026-09-27) sent the capped copy schema a 2995-character block. The model returned only the block's short tail (the question and its options, 89 and 215 characters), and both runs validated. A 1499-character block that the model copied whole validated in only 1 of 2 runs.

**Result:** a block longer than 1999 characters is detected only when the grader returns its short tail, so detection of very long prompts is unreliable rather than impossible. The validator's own ceiling remains 4096. Before v1.1.13 the schema was unbounded, and blocks up to 4096 characters could be copied whole, provided the model's output was not cut off at the token cap.

**Shipped constants** (`grader.py`):

LLAMA_CPP_MAX_REPETITION_THRESHOLD = 2000
GRAMMAR_MAX_STRING_LENGTH = LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1 = 1999

The measured values were highest pass 1999 and first failure 2000.
