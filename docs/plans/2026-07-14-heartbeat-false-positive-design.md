# d1389 — Fix Spurious "WAITING ON" Heartbeat Noise Design

> **Created:** 2026-07-14
> **Status:** Design Complete

## Summary

The daemon's periodic Slack heartbeat has been posting confusing "WAITING ON" entries that are either dead noise or occasional false positives. Root-cause investigation during brainstorming found the *actual* trigger differs from the directive's original diagnosis (which targeted phrases — "standing by", "no intervention needed" — that never reach the classifier because they don't match the `_AWAITING_PHRASE_RE` gate at all). Two independent, verified defects are being fixed instead:

1. **`notifications.py`** — `format_heartbeat` unconditionally renders a `⏳ WAITING ON COMMANDER: / there is nothing` block every time any real operator wait exists, because `commander_waits` is never populated by any `main.py` call site (confirmed dead scaffold param, `docs/plans/2026-07-14-heartbeat-two-section-waits-design.md`). This is pure noise co-rendered alongside every legitimate `WAITING ON {operator}` entry.
2. **`main.py`** — the awaiting-operator classifier (`_maybe_capture_operator_wait`) can be triggered by the Brain narrating that it's holding on an *automated* pipeline participant (a subagent, a Fable review, a blind-review tier) rather than the operator. `_AWAITING_PHRASE_RE`'s "holding"/"waiting on" alternatives let such phrases reach the LLM classifier; today's log shows one real misclassification of this shape ("needs to see the full function and the d1374 diff" — the Brain narrating its own investigation of this ticket).

## Architecture

Two independent fixes, two files, no shared state or ordering dependency:

- **Fix 0** (`notifications.py`): subtractive render-guard fix.
- **Fixes 1–3** (`main.py`): a fast-path exclusion regex ahead of the grader call, an extended classifier prompt, and a shorter TTL backstop.

## Components

**Fix 0 — `notifications.py::format_heartbeat`**

Wrap the COMMANDER section (header `⏳ *WAITING ON COMMANDER:*` plus its if/else body — the current lines rendering that header and its "there is nothing" filler) in `if commander_waits:` so the entire section is skipped when `commander_waits` is empty, rather than rendering an always-empty block. The operator-facing `WAITING ON {operator_name}` section's own logic is untouched — it already only runs inside `if waits or commander_waits:`, and after this fix, when `commander_waits` stays permanently `{}`, the outer gate reduces to `if waits:`, so the block only renders at all when there's a real operator entry to show.

**Fix 1 — `main.py`: new `_NOT_AWAITING_RE` fast-path exclusion**

```python
_NOT_AWAITING_RE = re.compile(
    r"(?:holding|waiting)\s+(?:for|on)\s+(?:the\s+)?"
    r"(?:subagent|sub-agent|worker|reviewer|fable|blind\s+review|tier[- ]?up|advisor)\b",
    re.IGNORECASE,
)
```

Checked in `_maybe_capture_operator_wait` immediately after the existing `_AWAITING_PHRASE_RE` gate and before the `self._grader.grade(...)` call — if it matches, return `False` immediately, skipping the LLM call. Targets phrases where the Brain is narrating that it's blocked on another automated pipeline participant (subagent, worker, reviewer, Fable, blind review, tier-up loop, advisor), not the human operator. Verified not to over-exclude genuine operator waits: none of the existing captured/test phrases ("waiting on your decision", "pinned decision needed", "holding for your approval on the migration") contain any of the audience nouns this pattern requires.

**Fix 2 — extend `_AWAITING_OP_SYSTEM` classifier prompt**

Add to the false-positive example list:
- `"holding for the Fable review result"` → false
- `"waiting on subagent verdict"` → false

**Fix 3 — `_OPERATOR_WAIT_TTL_SECONDS`: 1800 → 600**

Ensures any single missed false-positive classification cannot survive into a second heartbeat cycle (heartbeat interval 900s).

## Data Flow

Unchanged. `poll_brain_responses()` calls `_maybe_capture_operator_wait(text)` for every Brain message; the new fast-path regex short-circuits before the grader call for pipeline-audience phrasing. `post_heartbeat()` continues to merge `_operator_waits` with the deterministic `_get_pending_confirmation_waits()` result and pass it as `waits=` to `format_heartbeat`; `commander_waits` remains unpassed (still `{}`) — Fix 0 makes that safe to render.

## Error Handling

No new error paths. The fast-path regex is a pure string match (no I/O, cannot raise beyond a regex compile error caught at import time like the existing `_AWAITING_PHRASE_RE`). The render guard is a pure conditional with no side effects.

## Testing Strategy

1. `commander/tests/test_main_validate.py::TestOperatorWaits` — new cases: `"holding for the Fable review result"` and `"waiting on subagent verdict"` return `False` from `_maybe_capture_operator_wait` without calling `_grader.grade` (`assert_not_called()`); existing genuine-operator-wait phrases still reach the classifier unchanged.
2. New test for `format_heartbeat` (notifications test file) covering the actually-reachable asymmetric case: `waits` non-empty, `commander_waits` empty → output contains `WAITING ON {operator}` with real entries, does **not** contain `WAITING ON COMMANDER` or `there is nothing`. This is the regression guard for the confirmed defect; the existing `test_no_pending_confirmation_and_no_operator_waits_suppresses_section` only covers the both-empty case.
3. No new test for the classifier prompt text itself (consistent with existing practice — `test_d1362_style_system_narration_falls_through_when_grader_says_no` proves wiring, not real-grader behavior; a live grader call is out of scope for unit tests).
4. No dedicated TTL test — bare constant, already indirectly covered by existing `_prune_operator_waits` tests referencing the constant.

## Implementation Notes

- The directive's original Fix 1 (`_NOT_AWAITING_RE` matching "standing by", "no intervention needed", "monitoring") targeted phrases that never reach `_AWAITING_PHRASE_RE`'s positive gate and would be dead code; this design replaces those target phrases with ones confirmed reachable via the gate's "holding"/"waiting on" alternatives.
- `commander_waits` remains intentionally unwired (per the d1362 design doc) — Fix 0 makes the current permanently-empty state safe to render, it does not attempt to define or populate "waiting on commander" semantics.
- Unrelated, out of scope: the working tree currently has `post_heartbeat` passing `ollama_degraded=bool(ollama_degraded_urls())` to `format_heartbeat`, which has no such parameter in `notifications.py` — this is uncommitted, in-progress work from a concurrent directive (visible via the staged `ollama_client.py` circuit-breaker changes) and not part of this fix. Flagged for whoever finishes that work; will need a `TypeError` fix at that call site as a prerequisite for merging.
- `restart_daemon(directive_id=1389)` per directive spec, as the final execution step, to mark the directive completed before SIGHUP.
