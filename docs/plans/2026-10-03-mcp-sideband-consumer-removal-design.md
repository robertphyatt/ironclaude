# MCP Sideband Consumer Removal Design (v1.1.16 L2b-1d)

> **Created:** 2026-10-03
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-03-mcp-sideband-consumer-removal-requirements.md`
> **Loop:** full. This loop changes hook code.

## Summary

This loop removes the hook-side consumer of the MCP error sideband. MCP errors already reach the model that called the tool, as that tool's result. The consumer's only effect under context-only output was to deliver them a second time, into whichever session prompted next. The writer and the per-session-start truncation stay, so the log remains available for the operator to read.

## Approach

The operator chose to remove only the consumer: the change touches hooks and no TypeScript.

The rejected alternatives:

- **Remove the whole sideband**, including the writer. This needs a TypeScript change and a bundle rebuild, which is a larger change.
- **Filter by session.** The errors would still be duplicates, and the read-modify-write would race between sessions.
- **Revert to `"Error"`.** This leaves the consumer as dead code that still consumes the file.

## Components

1. **`state-activator.sh`.**
   - Delete the comment `# Surface any MCP tool errors from the sideband log` and the `surface_mcp_errors` call after it.
   - In the header comment's list of writers, remove `surface_mcp_errors`.
2. **`hook-logger.sh`.**
   - Delete the `surface_mcp_errors` function, from its comment block at `:600` through its closing brace at `:619`.
   - Keep `MCP_ERROR_LOG` at `:32`, because `session-init.sh:406` still uses it.
3. **`test-state-activator-single-object.sh`, case 5.**
   - Write `MCP-LINE-77 sideband failure` to the log and send the prompt `hello`.
   - Assert that the hook prints nothing and that the log still contains the line.
   - Against the current code this case is RED: the line is emitted and the file is moved away.

## Error Handling

No change. The fail-open rules are unaffected.

## Testing Strategy

- Case 5 goes RED then GREEN.
- Every other case in the file still passes.
- Run every hook suite and the Commander suite, then redeploy.
- Final gate: a Fable end review of the full combined diff must come back CLEAN. If it reports material findings, fix them and run the review again.
