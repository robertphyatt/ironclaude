# Episodic Memory: Read Codex Transcripts Design

> **Created:** 2026-10-09
> **Status:** Design Complete

Loop: full — size: ~140 lines of renderer in show.ts plus ~120 lines of tests and a snapshot (3 files, ~270 lines); no risk area (no DB writes, no MCP schema, no guards, no concurrency, no git).

## Requirements

Operator decisions (2026-10-09): next step "Codex read, then rollout (Rec.)"; approach "A: raw renderer (Rec.)". Purpose (operator): episodic memory must "deeply understand past conversations without missing a beat" and let the Commander act as the operator's avatar; one IronClaude for Claude and Codex.

- **R1.** `formatConversationAsMarkdown` (`show.ts:26`), used by the `read` MCP tool (`mcp-server.ts:311-316`) and the `show` CLI (`show-cli.ts:54`), detects a Codex rollout (records with `type` `session_meta` / `response_item` / `event_msg` / `turn_context` and a `payload`) and renders it instead of returning an empty string. Claude transcripts render exactly as today.
- **R2.** The Codex rendering, from the raw rollout lines: a header with session id, working directory, originator and CLI version (from `session_meta`, read from the whole file even when a line range is given); then, in order, user messages (`input_text`), assistant messages (`output_text`), tool calls (`function_call` / `custom_tool_call`: name and arguments) and tool outputs (`function_call_output` / `custom_tool_call_output`), each with its timestamp. Encrypted `reasoning`, `event_msg`, `turn_context` and other records are skipped.
- **R3.** `startLine` / `endLine` select the same 1-indexed raw lines that search reports for Codex exchanges (`codex-parser.ts` counts every line), so a search hit's range reads back exactly that exchange.
- **R4.** The `show` CLI's HTML mode renders the Codex markdown through the existing `marked` path (no separate HTML renderer).
- **R5.** Tests: the real fixture `__fixtures__/codex-rollout-sample.jsonl` renders a non-empty conversation containing its user and assistant text; the same fixture compacted to `.zst` reads identically through `readArchiveText`; a line range renders only that exchange, including when the file contains a blank line; a Claude transcript still renders unchanged (byte-for-byte snapshot taken from the current renderer); a malformed line is skipped, not fatal.

## Architecture

`show.ts` gains a small Codex branch: `formatConversationAsMarkdown` checks the parsed records and dispatches to `formatCodexRolloutAsMarkdown(jsonl, startLine, endLine)`. The read handler and CLI need no change. Out of scope: indexing tool outputs (deferred in the 2026-07-21 ingestion design), Codex AskUserQuestion answers, and the maintain rollout (next step).

## Error Handling

Unparseable lines are skipped (today a malformed line throws inside `JSON.parse`; the Codex branch tolerates them). An empty range returns an empty string, as today.

## Testing Strategy

TDD per R5 in the episodic-memory vitest suite, then the full episodic-memory suite and tsc; rebuild dist and refresh both plugin caches as part of the follow-on deploy.
