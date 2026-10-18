import { describe, it, expect } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, '__fixtures__', 'claude-askuser-session.jsonl');
const CODEX_FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');

describe('operator voice in the Claude parser', () => {
  it('indexes AskUserQuestion answers as a Q→A user message that starts a new exchange', async () => {
    const { parseConversation } = await import('./parser.js');
    const ex = await parseConversation(FIXTURE, 'proj', FIXTURE);
    expect(ex.length).toBe(2);
    expect(ex[0].userMessage).toBe('Plan the KIWI migration');
    expect(ex[0].assistantMessage).toBe('I need one decision first.');
    expect(ex[0].isAnswer).toBeUndefined();
    expect(ex[1].userMessage).toBe('Q: Which scope?\nA: Retrieval only');
    expect(ex[1].assistantMessage).toBe('Proceeding with retrieval only.');
    expect(ex[1].isAnswer).toBe(true);
    expect(ex[1].lineStart).toBe(4);
  });

  it('captures the session entrypoint on every exchange', async () => {
    const { parseConversation } = await import('./parser.js');
    const ex = await parseConversation(FIXTURE, 'proj', FIXTURE);
    expect(ex.map(e => e.entrypoint)).toEqual(['cli', 'cli']);
  });

  it('formats multi-select and non-string answers; ignores other tool results', async () => {
    const { formatAnswers } = await import('./parser.js');
    expect(formatAnswers({ answers: { 'Pick tools?': ['grep', 'rg'] } })).toBe('Q: Pick tools?\nA: grep, rg');
    expect(formatAnswers({ answers: { A: 'x', B: 'y' } })).toBe('Q: A\nA: x\n\nQ: B\nA: y');
    expect(formatAnswers({ answers: { N: 3 } })).toBe('Q: N\nA: 3');
    expect(formatAnswers('Answered')).toBe('');
    expect(formatAnswers({ questions: [] })).toBe('');
    expect(formatAnswers(undefined)).toBe('');
  });

  it('leaves entrypoint undefined when the transcript has none (older CLI, Brain)', async () => {
    const { parseConversation } = await import('./parser.js');
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'parser-voice-'));
    const f = path.join(tmp, 's.jsonl');
    fs.writeFileSync(f, [
      JSON.stringify({ type: 'user', message: { role: 'user', content: 'hi' }, timestamp: '2026-10-01T00:00:00.000Z' }),
      JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'hello' }] }, timestamp: '2026-10-01T00:00:01.000Z' }),
    ].join('\n') + '\n');
    const ex = await parseConversation(f, 'p', f);
    expect(ex.length).toBe(1);
    expect(ex[0].entrypoint).toBeUndefined();
    fs.rmSync(tmp, { recursive: true, force: true });
  });
});

describe('Codex provenance', () => {
  it('records session_meta.payload.originator as the entrypoint', async () => {
    const { parseCodexConversation } = await import('./codex-parser.js');
    const ex = await parseCodexConversation(CODEX_FIXTURE, 'p', CODEX_FIXTURE);
    expect(ex[0].entrypoint).toBe('Codex Desktop');
  });
});
