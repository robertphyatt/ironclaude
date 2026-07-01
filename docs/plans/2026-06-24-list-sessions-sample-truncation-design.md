# list_claude_sessions Sample Truncation Fix Design

> **Created:** 2026-06-24
> **Status:** Design Complete

## Summary

`list_claude_sessions` in `commander/src/ironclaude/orchestrator_mcp.py` appends up to 2000 characters of terminal sample output per session. When many tmux sessions exist, this produces 64KB+ output that the Brain cannot parse. The `sample` field is used only for confidence classification (`"ironclaude" in sample.lower()` / `"claude code" in sample.lower()`), which requires far fewer than 2000 characters.

## Architecture

Single constant change. No logic changes, no new abstractions, no interface changes.

## Components

- **File:** `commander/src/ironclaude/orchestrator_mcp.py`
- **Line:** 2698
- **Change:** `_strip_ansi(sample)[-2000:]` → `_strip_ansi(sample)[-200:]`

## Data Flow

No data flow changes. The `sample` field in returned candidate objects shrinks from ≤2000 chars to ≤200 chars per session.

## Error Handling

No error handling changes needed — the truncation slice behavior is identical.

## Testing Strategy

Run existing tests: `commander/tests/test_orchestrator_mcp.py`

No new tests required — the change is a constant reduction with no behavior change other than output size.

## Implementation Notes

200 chars is sufficient for confidence detection. The keywords `"ironclaude"` (9 chars) and `"claude code"` (11 chars) appear in terminal output and will be captured in any reasonable tail of session content.
