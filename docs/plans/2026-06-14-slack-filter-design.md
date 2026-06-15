# Slack Filter: Deterministic Directive-Reference Pre-filter Design

> **Created:** 2026-06-14
> **Status:** Design Complete
> **Directive:** d1133

## Summary

Brain responses to daemon system notifications (CHECK-IN, STUCK, CONTEXT REQUIRED, MANDATORY SWEEP, etc.) were reaching Slack when they should be silently discarded. The daemon's `poll_brain_responses()` path uses a single LLM-only barrier (`_validate_brain_message`) that fails open when Ollama is unavailable — every Brain text response passes through when Ollama is down.

The fix adds a **deterministic directive-reference pre-filter** as the first gate in `_validate_brain_message`. Conversational responses to system notifications never contain directive references (`#N`, `dN`, `directive N`). Blocking on this pattern is sufficient and Ollama-independent. A companion change in `poll_brain_responses` suppresses the `[CONTEXT REQUIRED]` reply for directive-missing messages, breaking the feedback loop where Brain's conversational ack triggers another `[CONTEXT REQUIRED]`, which triggers another ack, indefinitely.

## Architecture

Single-file change: `commander/src/ironclaude/main.py`.

Two paths exist for Brain messages to reach Slack:
1. **MCP `post_message` tool** — Brain explicitly calls this MCP tool; graded for proactiveness in `orchestrator_mcp.py`. Unaffected by this change.
2. **Daemon forwarding (`poll_brain_responses`)** — ALL Brain text output is captured here and forwarded if `_validate_brain_message` returns `(True, "")`. This is the broken path.

The fix adds a regex pre-filter to Path 2. The fail-open policy for Ollama errors is **preserved** for messages that already have directive references — this tradeoff is acceptable because a Brain message that includes `#1083` was almost certainly a deliberate Slack post attempt.

## Components

**New module-level constants** (near line 57, after `_BRAIN_MSG_SCHEMA`):

```python
_DIRECTIVE_REF_RE = re.compile(r'(?:#\d+|d\d+|directive\s+\d+)', re.IGNORECASE)
_BLOCKED_NO_DIRECTIVE = "no_directive_ref"
```

`_DIRECTIVE_REF_RE` matches `#1083`, `d1076`, `directive 42`. Does not match `dSomething`, bare `directive`, `#abc`. The `re` module is already imported.

**`_validate_brain_message` (lines 608-625)** — add pre-filter as first content check:

```python
def _validate_brain_message(self, text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "Empty message"
    if not _DIRECTIVE_REF_RE.search(text):          # [NEW]
        return False, _BLOCKED_NO_DIRECTIVE          # [NEW]
    result = self._grader.grade(...)
    if result.get("infrastructure_error"):
        return True, ""   # fail-open preserved (only for messages with directive refs)
    if result.get("valid", True):
        return True, ""
    return False, result.get("reason", "Message does not meet Brain message requirements")
```

**`poll_brain_responses` (lines 1079-1088)** — split blocked-message handling:

```python
if not valid:
    if reason == _BLOCKED_NO_DIRECTIVE:             # [NEW]
        logger.info(                                # [NEW]
            "Brain response dropped (no directive ref): %s", text[:100]  # [NEW]
        )                                           # [NEW]
        continue                                    # [NEW] — no CONTEXT_REQUIRED
    logger.warning("Brain message blocked: %s | text=%s", reason, text[:200])
    self.brain.send_message("[CONTEXT REQUIRED] ...")
    continue
```

## Data Flow

**Current (broken) path:**
```
Brain text → poll_brain_responses()
  → _validate_brain_message()
    → [empty check]
    → Ollama LLM call
      → Ollama down  → (True, "")   ← FAIL-OPEN: conversational response reaches Slack
      → valid=False  → (False, reason) → [CONTEXT REQUIRED] → Brain acks → loop
      → valid=True   → (True, "")  → Slack
```

**New path:**
```
Brain text → poll_brain_responses()
  → _validate_brain_message()
    → [empty check]
    → [NEW] regex check for #N / dN / directive N
      → no match → (False, _BLOCKED_NO_DIRECTIVE)
        → poll_brain_responses: logger.info, continue   ← no CONTEXT_REQUIRED
      → match    → Ollama LLM call
        → Ollama down → (True, "")   ← fail-open (directive ref present — acceptable)
        → valid=False → (False, reason) → [CONTEXT REQUIRED] (genuine post attempt)
        → valid=True  → (True, "") → Slack ✓
```

## Error Handling

- **Fail-open preserved:** `infrastructure_error` → `return True, ""` is unchanged. Only applies to messages that already have directive references after this fix.
- **Schema default unchanged:** `result.get("valid", True)` defaults True if field missing. This is guarded upstream by `LocalGrader`'s required-field check (`_BRAIN_MSG_SCHEMA` requires `"valid"`), which returns `infrastructure_error=True` if missing — routing to the fail-open branch. Dead code in practice.
- **No new exceptions:** `_validate_brain_message` retains its never-raise contract.
- **Logging level:** Directive-missing drops use `logger.info` (not `logger.warning`) — this is normal operation under the new filter, not an anomaly.

## Testing Strategy

New unit tests in `commander/tests/test_main.py`:

| # | Input text | Grader mock | Expected result | Expected poll action |
|---|---|---|---|---|
| 1 | `"That was conversational"` | not called | `(False, _BLOCKED_NO_DIRECTIVE)` | silent drop, no CONTEXT_REQUIRED |
| 2 | `"[CONTEXT REQUIRED] ack"` | not called | `(False, _BLOCKED_NO_DIRECTIVE)` | silent drop |
| 3 | `"#1134 fixed the bug with reason"` | `valid=True` | `(True, "")` | Slack post |
| 4 | `"#1134 update"` | `valid=False, reason="No reason clause"` | `(False, "No reason clause")` | CONTEXT_REQUIRED sent |
| 5 | `"#1134 update"` | `infrastructure_error=True` | `(True, "")` | Slack post (fail-open preserved) |
| 6 | `"Conversational ack"` | `infrastructure_error=True` | `(False, _BLOCKED_NO_DIRECTIVE)` | silent drop (pre-filter fires before grader) |

Regex unit tests (no mocking):
- **Match:** `#123`, `d456`, `D789`, `directive 10`, `DIRECTIVE 42`, `Directive 9`
- **No match:** `directive` (no number), `dSomething`, `#abc`, `d_none`, empty string

## Implementation Notes

- No changes to `orchestrator_mcp.py`, `grader.py`, or `notifications.py`.
- `re` module already imported in `main.py` (line 10).
- `_BLOCKED_NO_DIRECTIVE` is an internal sentinel — only used between `_validate_brain_message` and `poll_brain_responses`. Not surfaced to Brain or logged at warning level.
- The `[CONTEXT REQUIRED]` behavior for LLM-blocked messages is unchanged — Brain that includes a directive ref but omits the reason clause still gets corrective feedback.
