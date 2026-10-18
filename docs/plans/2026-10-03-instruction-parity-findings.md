# Instruction parity findings (Task 1, R7)

Date: 2026-10-03

SCRATCH = /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad

## Commands run (no reruns were needed)

Step 3 (Codex):

    codex exec -C SCRATCH/codex-memsearch-probe -s workspace-write --json --dangerously-bypass-hook-trust --output-last-message SCRATCH/codex-memsearch-last1.txt "Call the episodic-memory MCP server's search tool exactly once with the query \"instruction parity probe\", then reply DONE. Run no shell commands." > SCRATCH/codex-memsearch-run1.jsonl 2>&1

Exit 0.

Step 6 (Claude):

    cd SCRATCH/claude-task-probe && env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u CLAUDE_CODE_BRIDGE_SESSION_ID -u CLAUDE_CODE_EXECPATH -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_PID -u CLAUDE_EFFORT claude -p "Do exactly two things, in order, then reply DONE. 1. Use the Agent tool to dispatch one general-purpose subagent with the prompt: Reply OK and nothing else. 2. Call the episodic-memory MCP server's search tool once with the query: instruction parity probe. Run no shell commands and edit no files." --model sonnet --allowedTools Agent,mcp__plugin_ironclaude_episodic-memory__search --output-format stream-json --verbose > SCRATCH/claude-task-probe-run1.jsonl 2>&1

Exit 0. Count of "cannot be launched" in the output: 0.

## Step 4 outputs (Codex)

Control `grep -c -e '"type":"mcp_tool_call"' run1.jsonl`: 2

`grep -o -E '"server":"[^"]*","tool":"[^"]*"'`:

    "server":"episodic-memory","tool":"search"
    "server":"episodic-memory","tool":"search"

`jq -r .tool_name capture/PostToolUse.jsonl`:

    mcp__episodic_memory__search

S has exactly one value, so classification is ok.

Last message (codex-memsearch-last1.txt): `DONE`

## Step 7 outputs (Claude)

Agent control `grep -c -E '"name": ?"Agent"' run1.jsonl`: 1

agent.jsonl tool_name: `Agent`

task.jsonl tool_name: `Agent`

all.jsonl tool_name (in order):

    SubagentHandback
    Agent
    mcp__plugin_ironclaude_episodic-memory__search

C has exactly one value. Final "result" text: `DONE`

## Classification

CODEX_EPISODIC_SEARCH_TOOL=mcp__episodic_memory__search
CLAUDE_EPISODIC_SEARCH_TOOL=mcp__plugin_ironclaude_episodic-memory__search
CLAUDE_TASK_MATCHER_FIRES_FOR_AGENT=yes

## Notes

- Task result is `yes`, so no subagent-circuit-breaker observation applies.
- The nested Claude and Codex runs each inserted a session row into /Users/roberthyatt/.claude/ironclaude.db through session-init.sh. The rows are kept, not deleted.
- Step 9: before cleanup, the diff of the config snapshot against /Users/roberthyatt/.codex/config.toml was exactly the predicted hunk (`1845a1846,1848`: the codex-memsearch-probe `[projects."..."]` header, `trust_level = "trusted"`, and a blank line). No unrelated change was present.
