import { describe, it, expect } from 'vitest';
import path from 'path';
import { fileURLToPath } from 'url';
import { parseCodexConversation } from './codex-parser.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');

describe('parseCodexConversation', () => {
  it('extracts user/assistant text, tool calls, and session context; ignores event_msg + reasoning', async () => {
    const ex = await parseCodexConversation(FIXTURE, 'proj', FIXTURE);

    // Exactly one exchange despite the duplicate event_msg user/agent records.
    expect(ex.length).toBe(1);
    const e = ex[0];

    expect(e.userMessage).toContain('ZEBRA_MARKER_ingest_test');
    expect(e.assistantMessage).toContain('inspect the parser');
    // Encrypted reasoning must NOT leak into the assistant text.
    expect(e.assistantMessage).not.toContain('opaque');

    expect(e.sessionId).toBe('019f7742-abd8-7c62-af7b-fe07189f1ffd'); // from session_meta
    expect(e.cwd).toBe('/Users/roberthyatt/Code/ironclaude');         // from session_meta
    expect(e.claudeVersion).toBe('0.145.0-alpha.18');                 // cli_version
    expect(e.gitBranch).toBeUndefined();                              // absent in Codex
    expect(e.project).toBe('proj');

    expect(e.toolCalls && e.toolCalls.length).toBe(1);
    expect(e.toolCalls![0].toolName).toBe('shell');
    expect(e.toolCalls![0].toolInput).toEqual({ command: 'ls' });     // arguments JSON parsed
    expect(e.toolCalls![0].exchangeId).toBe(e.id);                    // back-filled

    expect(e.id).toMatch(/^[0-9a-f]{32}$/);                           // md5 hex
  });
});
