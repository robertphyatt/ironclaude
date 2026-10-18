# Codex UserPromptSubmit visibility and MCP hook capture (client-parity-hooks Task 3)

Date: 2026-10-03. Method: `codex exec` headless, scratch repo with a project `.codex/hooks.json`, `--dangerously-bypass-hook-trust`. No reruns were needed.

## Commands

Run 1 (UPS visibility):
```
codex exec -C <SCRATCH>/codex-ups-check -s workspace-write --json --dangerously-bypass-hook-trust --output-last-message <SCRATCH>/codex-ups-check-last1.txt "If your context contains a secret word, reply with exactly that word and nothing else. Otherwise reply NONE. Do not run any commands." > <SCRATCH>/codex-ups-check-run1.jsonl 2>&1
```
Run 3 (MCP capture):
```
codex exec -C <SCRATCH>/codex-ups-check -s workspace-write --json --dangerously-bypass-hook-trust --output-last-message <SCRATCH>/codex-ups-check-last3.txt "Call the state-manager MCP tool get_professional_mode exactly once, then reply DONE. Run no shell commands." > <SCRATCH>/codex-ups-check-run3.jsonl 2>&1
```
SCRATCH = /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad. Both exited 0.

## UPS visibility

`ls -la .../codex-ups-check/.codex` listed `ups-input.json` (612 bytes), `capture/`, `capture.sh`, `hooks.json`, `ups-marker.sh`. The hook ran. Last message (`codex-ups-check-last1.txt`): `PERIWINKLE-7731`.

UPS classification: **visible**. Consequence: queued warnings reach Codex at the next prompt via `hookSpecificOutput.additionalContext`. The UI was not tested; `exec` is headless.

## MCP hook capture (D9)

- Call-happened control (`grep -c get_professional_mode run3.jsonl`): `3`.
- PreToolUse `tool_name` (`jq -r .tool_name capture/PreToolUse.jsonl`): `mcp__state_manager__get_professional_mode`
- PostToolUse keys: `["cwd","hook_event_name","model","permission_mode","session_id","tool_input","tool_name","tool_response","tool_use_id","transcript_path","turn_id"]`
- Stop keys (two lines, run 1 and run 3): `["cwd","hook_event_name","last_assistant_message","model","permission_mode","session_id","stop_hook_active","transcript_path","turn_id"]`
- Last message run 3: `DONE`.
- O matches `^mcp__(plugin_ironclaude_)?state[-_]manager__get_professional_mode$` (checked with grep -E).

MCP classification: (a) O=mcp__state_manager__get_professional_mode envelope=tool_response

## Session rows codex exec inserted (kept, not deleted)

```
01a10255-56e8-78a2-8f9a-da9d0590ee25|undecided|idle   (run 1)
01a10255-a523-7a62-9d24-b3083e8bfdd4|undecided|idle   (run 3)
```

## config.toml trust entry

Before cleanup, `diff config.before.toml ~/.codex/config.toml` printed exactly one 3-line hunk (`1845a1846,1848`): the `[projects."<SCRATCH>/codex-ups-check"]` header, `trust_level = "trusted"`, and a blank line. No other change. Entry removed after this file was written (see the report for the post-removal diff).
