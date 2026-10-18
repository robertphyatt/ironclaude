# Findings: Codex strict schema for the fast model (2026-10-17)

## Reproduction and positive control (from the design's Evidence section)

Before the fix, `call_fast_model` on all three `main.py` schemas (`_BRAIN_MSG_SCHEMA`, `_AWAITING_OP_SCHEMA`, `_PROMPT_WAITING_SCHEMA`) returned `infrastructure_error` with `luna:gpt-5.6-luna exited 1`. The Codex error was HTTP 400 `invalid_json_schema`: "'schema.required' is required to include every key in properties. Missing 'reason'." A flat, fully required schema succeeded on both `gpt-5.6-luna` and `gpt-6-luna`, which is the positive control: the model and CLI work, and only the schema shape was rejected.

## RED (Step 1)

`red=1`, `5 failed, 33 passed`.
- AttributeError (no `strict_codex_schema`): `test_strict_codex_schema_closes_every_object_and_nullables_optional`, `test_strict_codex_schema_keeps_a_propertyless_object_closed`, `test_strict_codex_schema_covers_the_commander_schemas`.
- AssertionError: `test_codex_child_gets_strict_schema_and_claude_child_the_original` (Codex file was top-level-only closed), `test_codex_answer_drops_null_optional_keys_only` (None keys were kept).
- Passing at RED, as expected, because they guard unchanged behavior: `test_codex_answer_keeps_null_for_a_required_key`, `test_claude_answer_is_returned_unchanged_positive_control`.

## GREEN (Step 3)

`green=0`, `62 passed in 1.06s` (`tests/test_fast_model.py` and `tests/test_fast_model_chain.py`).

## Real Luna probe (Step 4)

`probe=0`. Log:

```
INFO:ironclaude.fast_model:fast model answered via luna:gpt-5.6-luna
INFO:ironclaude.fast_model:fast model answered via luna:gpt-5.6-luna
INFO:ironclaude.fast_model:fast model answered via luna:gpt-5.6-luna
_BRAIN_MSG_SCHEMA OK {"valid": true}
_AWAITING_OP_SCHEMA OK {"waiting_on": "neither", "worker_id": "d12"}
_PROMPT_WAITING_SCHEMA OK {"kind": "none", "interaction_block": null, "question": null, "options": [], "authority_text": null}
```

All three schemas now succeed; the same probe failed on the old code.

## Full Commander suite (Step 5)

`suite=0`, `4011 passed in 483.28s (0:08:03)`, no FAILED or ERROR lines.

## Known limits

- An enum containing null is accepted by the normaliser but UNVERIFIED live (no current Commander schema has an optional enum).
- The None strip is top-level only; nested items in current schemas are fully required, so nothing nested needs stripping today.

## Non-goals carried for the operator

- `_GRADER_VERDICT_SCHEMA` and the `_call_grader` Codex arm.
- Advisor Fallback Astra parity.
- The remaining end-review observations.
