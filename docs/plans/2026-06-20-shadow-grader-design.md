# Ollama/gemma4 Shadow Grader Design

> **Created:** 2026-06-20
> **Status:** Design Complete

## Summary

Add a shadow grading layer that runs in parallel with the existing Opus grader on spawn_worker, kill_worker, and approve_plan decisions. Both graders now use tools (Read, Bash for Opus; read_file, grep_files, git_diff for gemma4 via Ollama chat API) to investigate before producing a JSON verdict. Concordance reports — headlined by tool call sequences — are posted to Slack after every grading event. Shadow grader failures are informational only and never block the authoritative Opus path.

The primary signal for evaluating gemma4 readiness is tool call divergence: which tools each grader chose to call, in what order, with what arguments. Grade letter and pass/fail comparison are secondary.

## Architecture

Three connected layers:

**Authoritative Opus path (updated):** The Opus grader (Claude Code tmux session `ic-grader`) gets an updated prompt that permits tool use (Read, Bash) before returning the JSON verdict. The existing polling logic in `_do_grader_send_and_poll` already tolerates non-JSON output before the nonce delimiter — tool call log lines appear before the JSON and do not break polling. The method stores the full tmux delta in `self._last_grader_delta` before returning. Tool calls are extracted from this delta with best-effort regex parsing.

**Shadow gemma4 path (new):** A daemon thread fires after Opus returns and the caller already has the authoritative result. The thread calls `ShadowGrader.grade_with_tools()` which uses Ollama's `/api/chat` endpoint with three read-only tool definitions. It runs a tool-execution loop (max 5 steps) until gemma4 produces a JSON verdict. All tool executions are path-validated and subprocess-sandboxed.

**Concordance reporting:** Thread computes A/B/C/F concordance score, formats a Slack message with tool call sequences prominently first, then verdicts and score. Shadow exceptions are caught and logged; Opus result is never affected.

## Components

### New file: `commander/src/ironclaude/shadow_grader.py`

`ShadowGrader` class:
- `__init__(self, config_path=None)` — loads Ollama config, constructs OllamaClient
- `grade_with_tools(self, system_prompt, user_prompt, repo_path=None) -> dict` — runs tool-calling grade loop. Returns `{"grade", "approved", "feedback", "tool_calls": [{"name", "args"}, ...]}` on success, or `{"infrastructure_error": True, "error_detail": str, "tool_calls": []}` on failure.
- `_execute_tool(self, name, arguments, repo_path) -> str` — dispatches to read_file / grep_files / git_diff. Path-validates all inputs. Returns result string or `{"error": "..."}` JSON string on failure.
- `_validate_path(self, path, repo_path) -> None` — rejects `..`, requires path under `os.expanduser("~")`, `/tmp/`, or `repo_path`.

Tool definitions (Ollama OpenAI-compatible format):
```python
SHADOW_TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file by absolute path to examine its contents",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}
    }},
    {"type": "function", "function": {
        "name": "grep_files",
        "description": "Search for a pattern in files under a directory",
        "parameters": {"type": "object",
                       "properties": {
                           "pattern": {"type": "string"},
                           "directory": {"type": "string"}
                       },
                       "required": ["pattern", "directory"]}
    }},
    {"type": "function", "function": {
        "name": "git_diff",
        "description": "Show uncommitted git changes in a repository",
        "parameters": {"type": "object",
                       "properties": {"repo_path": {"type": "string"}},
                       "required": ["repo_path"]}
    }},
]
MAX_TOOL_STEPS = 5
```

### Modified: `commander/src/ironclaude/ollama_client.py`

Add `post_chat(self, messages, tools=None) -> tuple[str, list[dict]]`:
- POSTs to `/api/chat`
- Returns `(response_content_str, tool_calls_list)` where `tool_calls_list` is a list of `{"name": str, "arguments": dict}` from `message.tool_calls`
- Uses same primary/fallback URL pattern as `_post`
- Raises `OllamaConnectionError` or `OllamaTimeoutError` on failure
- Strips `<think>` tags and special tokens from response content before returning (same as `LocalGrader`)

### Modified: `commander/src/ironclaude/orchestrator_mcp.py`

**New instance variables on OrchestratorTools.__init__:**
```python
self._last_grader_delta: str = ""
self._shadow_grader = ShadowGrader(config_path=self._ollama_config_path)
```

**New methods:**
- `_parse_tool_calls_from_delta(delta: str) -> list[dict]` — regex for Claude Code tool patterns: `[●•]\s*(\w+)\(([^)]*)\)` and JSON `{"type": "tool_use", "name": ...}` blocks. Returns `[{"tool": str, "args": str}, ...]`.
- `_compute_concordance(opus: dict, shadow: dict) -> str` — "A" exact match (grade + pass/fail), "B" same pass/fail different grade, "C" different pass/fail, "F" infrastructure_error.
- `_format_shadow_slack_message(context, worker_id, opus_result, opus_tool_calls, shadow_result, concordance) -> str` — builds Slack message (see format below).
- `_run_shadow_and_report(context, worker_id, repo, opus_result, opus_tool_calls, system_prompt, user_prompt) -> None` — thread target. Catches all exceptions; logs and returns silently on any error.
- `_fire_shadow_thread(context, worker_id, repo, opus_result, opus_tool_calls, system_prompt, user_prompt) -> None` — starts daemon thread.

**Modified `_do_grader_send_and_poll`:** Before each `return` path, store `self._last_grader_delta = delta`. On the timeout/None path, store `self._last_grader_delta = ""`.

**Updated Opus grader prompts** (spawn_worker, kill_worker, approve_plan): Replace the line `"Respond with valid JSON only — no markdown, no explanation:"` with:
```
You may use Read and Bash to investigate before responding. When ready,
output ONLY a valid JSON object on a single line:
```
The polling regex (`"grade"\s*:\s*"[ABCDF]"`) is unchanged and finds the JSON wherever it appears after the nonce delimiter.

**Shadow thread firing** added to:
- `spawn_worker` — after `grade_result = self._call_grader(...)` (both escalation paths at lines ~1602 and ~1609)
- `kill_worker` — after `grade_result = self._call_grader(...)` (line ~2508)
- `approve_plan` — after `grade_result = self._call_grader(...)` (line ~2112)

For kill_worker and approve_plan: `repo` is looked up via `self.registry.get_worker(worker_id).get("repo")`. If None, passed as None to `grade_with_tools`.

## Data Flow

```
spawn_worker / kill_worker / approve_plan  [main thread]
  │
  ├─ _call_grader(system_prompt, user_prompt)         [Opus, blocking]
  │     └─ _do_grader_send_and_poll()
  │           ├─ Sends prompt to ic-grader tmux
  │           ├─ Opus may call Read/Bash to investigate
  │           ├─ Polls until JSON found after nonce delimiter
  │           └─ Stores full delta → self._last_grader_delta
  │
  ├─ opus_tool_calls = _parse_tool_calls_from_delta(self._last_grader_delta)
  ├─ _fire_shadow_thread(context, worker_id, repo, opus_result,
  │                       opus_tool_calls, system_prompt, user_prompt)
  └─ return opus_result to Brain   [immediate, no wait on shadow]

                   [Background daemon thread]
                   ├─ _shadow_grader.grade_with_tools(sys_prompt, usr_prompt, repo)
                   │    ├─ ollama_client.post_chat(messages, SHADOW_TOOLS)
                   │    ├─ [loop ≤ MAX_TOOL_STEPS=5]
                   │    │   ├─ if tool_calls: execute → feed tool result → post_chat
                   │    │   └─ else: parse JSON verdict from content → break
                   │    └─ return {grade, approved, feedback, tool_calls:[...]}
                   ├─ _compute_concordance(opus_result, shadow_result)
                   ├─ _format_shadow_slack_message(...)
                   └─ self._slack.post_message(msg)  [or logger.info if no Slack]
```

## Error Handling

| Failure | Behavior |
|---|---|
| Opus tool call fails | Claude handles and continues — no change to existing behavior |
| gemma4 Ollama unreachable | `infrastructure_error` → concordance "F" → Slack posts with error detail |
| Tool subprocess fails | Return `{"error": "..."}` as tool result to gemma4, continue loop |
| Path traversal in tool arg | Return `{"error": "path not allowed"}`, no subprocess |
| Tool loop exceeds MAX_TOOL_STEPS | Stop loop, parse final content as JSON verdict |
| Shadow thread exception (any) | Caught at top of `_run_shadow_and_report`, logged, silent |
| Slack unavailable in shadow thread | Log warning, no exception |
| `_last_grader_delta` race (unlikely) | Wrong tool calls in shadow report — accepted; informational only |
| `registry.get_worker` returns no repo | `repo_path=None` — ShadowGrader skips repo-relative tools gracefully |

## Slack Report Format

Tool call sequences appear first as the primary diagnostic signal:

```
🔬 Shadow Grader — spawn_worker | worker-123

Tool Calls (primary signal):
  Opus:    ● Read(file_path="/repo/.ironclaude/brain-notes.md")
           ● Bash(command="git log --oneline -5")
           [2 calls]
  gemma4:  read_file("/repo/.ironclaude/brain-notes.md")
           grep_files("TODO", "/repo/src")
           [2 calls]

Verdicts:
  Opus:    B ✓ approved
           "Clear objective with file paths, appropriate worker type"
  gemma4:  C ✗ rejected
           "Missing explicit success criteria"

Concordance: C — DIVERGE on pass/fail
```

On gemma4 infrastructure failure:
```
  gemma4:  (infrastructure error — Ollama unreachable)

Concordance: F — gemma4 failed
             Detail: Ollama timed out after 120s
```

Concordance key:
- **A** — exact match (same grade letter, same pass/fail)
- **B** — same pass/fail, different grade letter
- **C** — different pass/fail (highest-priority divergence)
- **F** — gemma4 infrastructure error or unparseable output

## Testing Strategy

**New file `tests/test_shadow_grader.py`:**
- `grade_with_tools` — mock `post_chat`: single tool call → execute → final verdict; multi-step loop; max-steps cutoff; infrastructure error on first call; invalid JSON in final content
- `_execute_tool` — read_file success (mock file read), path traversal rejection (`..`), grep_files subprocess mock, git_diff subprocess mock, subprocess timeout

**Updated `tests/test_ollama_client.py`:**
- `post_chat` — mock `requests.post`, verify `/api/chat` request payload, verify `tool_calls` parsed from response, verify fallback URL used on primary failure

**Updated `tests/test_orchestrator_mcp.py`:**
- `_parse_tool_calls_from_delta` — empty string, single `● Read(...)` pattern, multiple tools, no match, JSON tool_use block
- `_compute_concordance` — all four values (A: same grade+pass/fail, B: same pass/fail diff grade, C: diff pass/fail, F: infrastructure_error)
- `_format_shadow_slack_message` — verify tool calls section appears before verdicts; verify concordance label; verify F-case format
- Shadow integration — mock `_call_grader` + `grade_with_tools` + Slack; call `spawn_worker`/`kill_worker`/`approve_plan`; assert thread fires; assert Slack post contains concordance; assert Opus result returned immediately unchanged

All existing spawn_worker, kill_worker, approve_plan tests must pass without modification.

## Implementation Notes

- `GRADER_TIMEOUT_SECONDS = 300` is sufficient for tool-using Opus grader. No change.
- gemma4's tool calling uses Ollama's OpenAI-compatible format — `tools` array in chat payload, `tool_calls` in response `message` field. Verify gemma4:12b-it-qat supports tool calling via `/api/chat` before implementation; if not, try `gemma4:27b` or `gemma4:12b`.
- `_last_grader_delta` is set inside `_grader_lock` scope (inside `_do_grader_send_and_poll`) and read immediately after `_call_grader` returns. The race window (another grader call overwriting before read) is narrow and consequence is informational-only incorrect tool list in shadow report.
- For `_fire_shadow_thread` in spawn_worker: there are two Opus call sites (infrastructure error at line ~1602, low-confidence at line ~1609). Shadow thread is fired after each separately. The shadow grader gets the same `system_prompt` and `user_prompt` that was passed to `_call_grader`.
- Worker registry `get_worker` field for repo: verify exact field name when implementing (likely `"repo"` based on spawn_worker parameters, but may be stored differently in the registry dict).
- ShadowGrader uses the same Ollama config path as LocalGrader. If a separate shadow model is desired, it can be added to the config under a `shadow_model` key; otherwise falls back to the default `gemma4:12b-it-qat`.
