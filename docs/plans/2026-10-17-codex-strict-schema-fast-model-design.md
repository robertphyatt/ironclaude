# Codex Strict Schema for the Fast Model Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: full. The change sits at the LLM-output trust boundary of the Commander's fast-model helper.

## Summary

The end review of the per-call difficulty tiering effort (2026-10-17, fresh Opus reviewer) found one MATERIAL defect, M1. The Codex arm of `commander/src/ironclaude/fast_model.py` `call_fast_model` adds `additionalProperties: false` only at the top level of the schema. Codex strict structured output also requires every object node to have `additionalProperties: false` and to list every property in `required`. So Luna rejects any schema with an optional property or a nested object.

Operator authority (2026-10-16, verbatim): "religiously still follow pm even though the hooks are off until the plan is successfully executed. Then commit and redeploy". The executing-plans Step 7.5 end review is part of that plan, and it requires every verified MATERIAL finding to be fixed before the effort is complete. Haiku class means Luna when Codex runs (operator, 2026-10-16: "treating "haiku" as "haiku class" = "luna" if codex is running").

Delegated authority is in effect: the operator is asleep and gave the instruction above. The scope decisions below record `Predicted answer: … (delegated)`.

## Evidence (measured 2026-10-17)

- Reproduction: `call_fast_model` with a codex-only grader config on `_BRAIN_MSG_SCHEMA`, `_AWAITING_OP_SCHEMA` and `_PROMPT_WAITING_SCHEMA` (`main.py`) → `infrastructure_error`, "luna:gpt-5.6-luna exited 1", for all three.
- Positive control on both `gpt-5.6-luna` and `gpt-6-luna`: a flat schema with every property required succeeds (rc 0, structured answer). `_BRAIN_MSG_SCHEMA` fails with HTTP 400 `invalid_json_schema`: "'schema.required' is required to include every key in properties. Missing 'reason'."
- Why the earlier work missed it: every test stubs `_run_child`, and the fallback loop's Luna latency probe used a flat, fully-required schema.
- Live impact on this box: the grader role is `{"preferred": "claude", "clients": ["claude", "codex"]}`. Luna is reached only after Haiku has failed, and then C1 (Brain-message validator), C2 (awaiting-operator capture) and C3 (prompt detection) fail open. With a codex-preferred grader they would fail open on every call. The hook schemas are flat with every property required, so the hooks are unaffected.

## Requirements

- **R1 — Strict normaliser.** `fast_model.py` gains a pure function that returns a strict copy of a JSON schema. For every object node (any node with `properties`), recursively through `properties`, `items` and `anyOf`/`oneOf`:
  - it sets `additionalProperties: false`;
  - it sets `required` to every property key, in property order;
  - each property that was not originally required becomes nullable: a string `type` becomes `[type, "null"]`, a list `type` gains `"null"` if absent, and a property with no `type` (for example an `enum`-only property) gains `"null"` in its `enum`.
  
  The input schema is never mutated.
- **R2 — Codex arm only.** `call_fast_model` sends the normalised schema only to the Codex child. The Claude `--json-schema` argv and the local-box path stay byte-identical.
- **R3 — Shape restored.** After parsing a Codex answer, `call_fast_model` removes top-level keys whose value is `None` when the original schema did not require them. Consumers then see the same shape a Claude answer gives. This preserves `main.py:2102` `result.get("reason", "Message does not meet Brain message requirements")`. The existing required-field check still runs against the original `required` list.
- **R4 — Tests.** Unit tests cover:
  - the normaliser on a schema with an optional property, a nested object, an already-nullable property and an `enum`-only property, plus input immutability;
  - `call_fast_model` sending the strict schema to the Codex child and the unchanged schema to the Claude child;
  - stripping a `None` optional key from a Codex answer while keeping a non-`None` one.
- **R5 — Real probe.** After the change, one real `codex exec` per schema (`_BRAIN_MSG_SCHEMA`, `_AWAITING_OP_SCHEMA`, `_PROMPT_WAITING_SCHEMA`) through `call_fast_model` with a codex-only config must return a structured answer, not `infrastructure_error`. The output is recorded in the findings.

Predicted answer: yes (delegated) — scope as above.

## Non-goals

These are recorded for the operator; they are not changed here.

- **Boy Scout, pre-existing:** `orchestrator_mcp.py` `_GRADER_VERDICT_SCHEMA` omits `recommended_model` from `required`, so every Codex `_call_grader` call is rejected the same way (rollout 2026-10-11). The fix changes the Opus-class grader's accepted output, so it needs the operator's go.
- **Observation 5:** the Advisor Fallback text has Claude map `fable → opus`, while a Codex Astra session uses `"same"` (Astra reviews Astra). The broker cannot cap at Sol, so this needs an operator decision.
- **Hook `jq` parity:** hook schemas are flat and fully required, so there is no defect today.
- The other end-review observations go to the backlog.
