# LLM Grading: Replace Regex/Keyword Semantic Judgment Design

> **Created:** 2026-06-10
> **Status:** Design Complete

## Summary

Three semantic keyword/regex patterns in two daemon-side files make content quality judgments via brittle string matching. These are replaced with LLM grading calls using a new standalone `LocalGrader` class in `grader.py`. The grader infrastructure already exists in `OrchestratorTools` (orchestrator_mcp.py) but is inaccessible from the daemon and BrainClient — extracting it to a shared module makes all callers share one implementation.

Targets:
1. `brain_client.py` — `_PERMISSION_SEEKING_RE` + `_check_permission_seeking`
2. `main.py` — `_DIRECTIVE_REF_RE` + `_REASON_KEYWORDS` + `_validate_brain_message`
3. `main.py` — `PROMPT_PATTERNS` + `_detect_prompt_waiting`

No-touch: hooks (already LLM-based via `call_validation_llm`), `orchestrator_mcp.py` regex for JSON extraction, ANSI stripping, `<think>` tag removal.

## Architecture

New `grader.py` module with `LocalGrader` class. `OrchestratorTools._call_local_grader` becomes a thin delegation wrapper. `main.py` and `brain_client.py` import and instantiate `LocalGrader` directly.

## Components

### `grader.py` (new)

`LocalGrader` class:
- `__init__(config_path=None)` — lazy-initializes `OllamaClient` from `~/.claude/ironclaude-hooks-config.json` (same path and fallback logic as `OrchestratorTools._get_ollama_client`)
- `grade(system_prompt, user_prompt, schema=None) -> dict` — POST to Ollama, strip `<think>` tags, parse JSON, enforce schema required fields
- `_build_infrastructure_error(detail) -> dict` — returns `{"infrastructure_error": True, "error_detail": detail}`
- All error paths return `_build_infrastructure_error(...)` — never raises

### `orchestrator_mcp.py` (modified)

`_call_local_grader` becomes:
```python
def _call_local_grader(self, system_prompt, user_prompt, format_schema):
    return self._local_grader.grade(system_prompt, user_prompt, format_schema)
```
`self._local_grader = LocalGrader(config_path=self._ollama_config_path)` added to `__init__`.
`_get_ollama_client()` and `_ollama_cfg_cache` remain (used by `_get_worker_command`).

### `main.py` (modified)

- `self._grader = LocalGrader()` added to `IroncladeDaemon.__init__`
- `self._prompt_waiting_cache: dict[int, tuple[float, bool]] = {}` added to `__init__`
- `_validate_brain_message` — replace keyword/regex with `self._grader.grade(BRAIN_MSG_VALIDATION_SYSTEM, text, schema)`, fail-open on infrastructure error
- `_detect_prompt_waiting` — replace `PROMPT_PATTERNS` loop with `self._grader.grade(PROMPT_WAITING_SYSTEM, log_tail[-2000:], schema)`, 120s TTL cache on `hash(log_tail)`, fail-safe returns `False`
- Remove: `_DIRECTIVE_REF_RE`, `_REASON_KEYWORDS`, `PROMPT_PATTERNS`

### `brain_client.py` (modified)

- `self._grader = LocalGrader()` added to `BrainClient.__init__`
- `_check_permission_seeking` — replace `_PERMISSION_SEEKING_RE` with `self._grader.grade(PERMISSION_SEEKING_SYSTEM, text, schema)`, throttle logic preserved, fail-open returns `None`
- Remove: `_PERMISSION_SEEKING_RE` class constant

### `tests/test_grader.py` (new)

Unit tests for `LocalGrader.grade()` mocking `OllamaClient.post_generate`.

## Data Flow

```
Brain message validation:
  poll_brain_responses → _validate_brain_message → LocalGrader.grade → OllamaClient
    infrastructure_error → (True, "")        [fail-open]
    valid=False          → (False, reason)   [CONTEXT REQUIRED sent to Brain]
    valid=True           → Slack post

Permission-seeking correction:
  _brain_session → _check_permission_seeking → LocalGrader.grade → OllamaClient
    infrastructure_error → None              [fail-open, skip correction]
    seeking=False        → None
    seeking=True+throttle → CORRECTION_MESSAGE → Brain

Stuck detection:
  _check_stuck_workers → _detect_prompt_waiting → cache hit? → cached result
                                                → cache miss → LocalGrader.grade → OllamaClient
    infrastructure_error → False              [fail-safe, NOT cached]
    waiting=bool         → cache(hash, result, now), return result

OrchestratorTools._call_local_grader:
  spawn_worker/send_to_worker/etc. → _call_local_grader → self._local_grader.grade
    (same return shape as before, no behavior change at call sites)
```

## Error Handling

| Caller | Ollama failure policy | Rationale |
|--------|-----------------------|-----------|
| `_validate_brain_message` | fail-open → `(True, "")` | Don't silently drop Brain messages; CONTEXT REQUIRED noise is worse than an occasional pass-through |
| `_check_permission_seeking` | fail-open → `None` | Missing one correction < false-blocking the Brain |
| `_detect_prompt_waiting` | fail-safe → `False` | "Not waiting" applies shorter staleness thresholds — conservative when uncertain |
| `_call_local_grader` (OrchestratorTools) | unchanged — returns `infrastructure_error` dict | 7 existing call sites already handle it |

`LocalGrader.grade()` handles: `OllamaError` subclasses, empty response, non-JSON response, missing required schema fields. All return `_build_infrastructure_error(...)`. Never raises.

Infrastructure errors are NOT cached in `_detect_prompt_waiting` — next staleness cycle retries the grader.

## Testing Strategy

### `tests/test_grader.py` (new)
Mock `OllamaClient.post_generate` via `unittest.mock.patch`:
- Happy path — valid JSON → parsed dict returned
- `OllamaError` → `infrastructure_error` dict
- Empty response → `infrastructure_error`
- Non-JSON → `infrastructure_error`
- `<think>` tags stripped before parse
- Schema required-field violation → `infrastructure_error`
- Config absent → localhost defaults, no crash
- All error paths use `_build_infrastructure_error` (consistent shape)

### Modified method tests
Mock `LocalGrader.grade` at the method level (follow existing pattern: `mock_grader.grade = MagicMock(...)`).

`_validate_brain_message`: valid=True passes, valid=False triggers CONTEXT REQUIRED, infrastructure_error is fail-open, empty input short-circuits.

`_check_permission_seeking`: seeking=True+throttle=ok returns CORRECTION_MESSAGE, seeking=False returns None, infrastructure_error returns None, throttle at limit returns None.

`_detect_prompt_waiting`: waiting=True cached, second call within TTL skips grader (call count = 1), post-TTL retries grader, infrastructure_error returns False and is NOT cached, log tail truncated to 2000 chars verified.

## Implementation Notes

- `LocalGrader` is instantiated once per object (`IroncladeDaemon.__init__`, `BrainClient.__init__`, `OrchestratorTools.__init__`) — config loaded lazily on first `grade()` call
- `_check_permission_seeking` no longer needs the `re.split(r'[.!?]\s+', ...)` sentence extraction — the LLM prompt instructs it to focus on the final sentence
- The `CORRECTION_MESSAGE` class constant and throttle logic in `BrainClient` are preserved unchanged
- `_detect_prompt_waiting` cache grows at most to the count of concurrently running workers (bounded, small)
- LLM grading prompts (BRAIN_MSG_VALIDATION_SYSTEM, PERMISSION_SEEKING_SYSTEM, PROMPT_WAITING_SYSTEM) are module-level constants in their respective files — not in `grader.py`, which stays prompt-agnostic
