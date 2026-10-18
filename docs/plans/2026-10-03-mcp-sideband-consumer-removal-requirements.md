# MCP Sideband Consumer Removal (v1.1.16 L2b-1d) Requirements

> **Created:** 2026-10-03
> **Status:** Operator-approved.
> **Parent:** `docs/plans/2026-10-03-parity-output-path-fixes-requirements.md` (L2b-1c)

## Operator decisions

The operator made these choices after the L2b-1c Fable end review:

1. **"Remove the sideband (Rec.)"**: stop delivering MCP sideband errors into context on both clients.
2. **"Remove the consumer only (Rec.)"**: keep the state-manager writer. `~/.claude/ironclaude-errors.log` stays as an operator debug log, and `session-init` still truncates it at every session start.
3. **"We need a final passing fable review though"**: before the operator commits, a Fable end review of the full combined L2b-1, L2b-1b, L2b-1c and L2b-1d diff must come back CLEAN. If it reports material findings, they are fixed and Fable reviews again until it passes.

## Finding (verified)

**The state-manager writes every session's errors to one shared file.** `worker/mcp-servers/state-manager/src/index.ts:31-41` and `:150` append every tool error from every session to the global `~/.claude/ironclaude-errors.log`. The only place a session is identified is the `session=<id>` text inside each line.

**The consumer delivers the whole file to whichever session prompts next.** `surface_mcp_errors` (`worker/hooks/hook-logger.sh:600-619`) does two things on every prompt, called from `state-activator.sh`:
- It moves the whole file away, so its contents are consumed.
- It emits every line, with no check on which session wrote it.

**L2b-1c turned this delivery on.** It changed `"Error"` to `"ERROR"` at `:614`, which lets these lines past the gate into context. The result:
- Another session's errors reach the wrong model.
- The first session to prompt consumes the file, so the session that caused the error loses its copy.
- Each error is a duplicate: `index.ts:39` notes that the error is still returned to the calling model as the tool result.

**Origin (episodic memory, 2026-02-17):** the sideband was meant to make MCP failures visible to the operator. Since the initial commit, the `"Error"` gate has kept it from being displayed.

## Requirements

**R1:** `state-activator.sh` no longer calls `surface_mcp_errors`. Its header comment no longer lists it as a writer.

**R2:** `hook-logger.sh` no longer defines `surface_mcp_errors`. `MCP_ERROR_LOG` stays, because `session-init.sh:406` uses it.

**R3:** Test case 5 is changed to cover the removal.
- **Setup:** a line in `ironclaude-errors.log`, then a prompt.
- **Pass:** the hook prints nothing, and the line is still in the file.
- **RED first:** the case must fail against the current code.

**R4:** Run every hook suite and the Commander suite. Then redeploy:
- `deploy-hooks`;
- diff the Claude plugin's `hooks.json`, and copy it only if it changed;
- `codex plugin add`, followed by the cache `diff -rq`;
- write the deploy log.

**R5:** The final Fable end review of the full combined staged diff must pass, as set by operator decision 3. Only then does the operator commit L2b-1, L2b-1b, L2b-1c and L2b-1d together and re-trust the 4 Codex hooks.

## Out of scope

- The state-manager writer and its stale comment at `index.ts:30` ("a file that hooks can surface").
- The L2b-1c minor backlog.
