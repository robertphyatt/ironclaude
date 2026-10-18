import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import zlib from 'zlib';
import { fileURLToPath } from 'url';
import { formatConversationAsMarkdown, formatConversationAsHTML } from './show.js';
import { readArchiveText } from './archive-io.js';
import { parseCodexConversation } from './codex-parser.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const CODEX_FIXTURE = path.join(here, '__fixtures__', 'codex-rollout-sample.jsonl');
const CLAUDE_FIXTURE = path.join(here, '__fixtures__', 'claude-askuser-session.jsonl');
const SESSION = '019f7742-abd8-7c62-af7b-fe07189f1ffd';

function record(type: string, payload: object, timestamp: string): string {
  return JSON.stringify({ timestamp, type, payload });
}

// A second exchange appended to the real fixture: a custom tool call with array output
// (one block holds a ``` run), a function call with string output, then the reply.
const SECOND_EXCHANGE = [
  record('response_item', { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Now handle OKAPI_MARKER_second please.' }] }, '2026-07-18T22:06:00.000Z'),
  record('response_item', { type: 'custom_tool_call', name: 'exec', input: 'run(`ls`)', call_id: 'c2' }, '2026-07-18T22:06:01.000Z'),
  record('response_item', { type: 'custom_tool_call_output', call_id: 'c2', output: [{ type: 'input_text', text: 'Script completed\nOutput:\n' }, { type: 'input_text', text: 'has ``` fence inside' }] }, '2026-07-18T22:06:02.000Z'),
  record('response_item', { type: 'function_call', name: 'shell', arguments: '{"command":"pwd"}', call_id: 'c3' }, '2026-07-18T22:06:03.000Z'),
  record('response_item', { type: 'function_call_output', call_id: 'c3', output: 'PANGOLIN_OUTPUT_marker' }, '2026-07-18T22:06:04.000Z'),
  record('response_item', { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Done with OKAPI_MARKER_second.' }] }, '2026-07-18T22:06:05.000Z'),
];

let tmp: string;
let fixture: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'show-codex-'));
  fixture = fs.readFileSync(CODEX_FIXTURE, 'utf-8');
});
afterEach(() => { fs.rmSync(tmp, { recursive: true, force: true }); });

describe('formatConversationAsMarkdown on Codex rollouts', () => {
  it('renders the real Codex fixture with session metadata, messages and tool calls', () => {
    const md = formatConversationAsMarkdown(fixture);
    expect(md).toContain(`**Session ID:** ${SESSION}`);
    expect(md).toContain('**Working Directory:** /Users/example/Code/ironclaude');
    expect(md).toContain('**Originator:** Codex Desktop');
    expect(md).toContain('**Codex CLI Version:** 0.145.0-alpha.18');
    expect(md).toContain('### **User** (2026-07-18T22:05:02.284Z)\n\nPlease help with ZEBRA_MARKER_ingest_test in the parser.');
    expect(md).toContain("### **Agent** (2026-07-18T22:05:08.839Z)\n\nSure, I'll inspect the parser and run the tests.");
    expect(md).toContain('### **Tool Call:** `shell` (2026-07-18T22:05:09.000Z)');
    expect(md).toContain('"command": "ls"');
    // Encrypted reasoning and the event_msg duplicates are skipped.
    expect(md).not.toContain('opaque');
    expect(md.split('ZEBRA_MARKER_ingest_test').length - 1).toBe(1);
  });

  it('reads a compacted .zst copy identically through readArchiveText', () => {
    const logical = path.join(tmp, 'rollout.jsonl');
    fs.writeFileSync(logical + '.zst', zlib.zstdCompressSync(Buffer.from(fixture)));
    const raw = formatConversationAsMarkdown(fixture);
    expect(raw).toContain('ZEBRA_MARKER_ingest_test');
    expect(formatConversationAsMarkdown(readArchiveText(logical))).toBe(raw);
  });

  it('renders exactly the exchange a search hit points at, with whole-file metadata', async () => {
    const file = path.join(tmp, 'two.jsonl');
    // The leading '\n' leaves a blank line 8 that codex-parser counts, so a renderer
    // that drops blank lines shifts the second range and loses its user message.
    const text = fixture + '\n' + SECOND_EXCHANGE.join('\n') + '\n';
    fs.writeFileSync(file, text);
    const exchanges = await parseCodexConversation(file, 'proj', file);
    expect(exchanges.length).toBe(2);

    const first = formatConversationAsMarkdown(text, exchanges[0].lineStart, exchanges[0].lineEnd);
    expect(first).toContain('ZEBRA_MARKER_ingest_test');
    expect(first).not.toContain('OKAPI_MARKER_second');

    const second = formatConversationAsMarkdown(text, exchanges[1].lineStart, exchanges[1].lineEnd);
    expect(second).toContain(`**Session ID:** ${SESSION}`);
    expect(second).toContain('Now handle OKAPI_MARKER_second please.');
    expect(second).toContain('### **Tool Call:** `exec` (2026-07-18T22:06:01.000Z)\n\n```\nrun(`ls`)\n```');
    expect(second).toContain('````\nScript completed\nOutput:\n\nhas ``` fence inside\n````');
    expect(second).toContain('"command": "pwd"');
    expect(second).toContain('PANGOLIN_OUTPUT_marker');
    expect(second).toContain('Done with OKAPI_MARKER_second.');
    expect(second).not.toContain('ZEBRA_MARKER_ingest_test');
  });

  it('skips malformed lines instead of failing', () => {
    const lines = fixture.split('\n');
    lines.splice(2, 0, '{not json');
    lines.unshift('garbage first line');
    const md = formatConversationAsMarkdown(lines.join('\n'));
    expect(md).toContain('ZEBRA_MARKER_ingest_test');
    expect(md).toContain("Sure, I'll inspect the parser and run the tests.");
  });

  it('leaves Claude transcripts on the existing renderer, byte for byte', () => {
    const md = formatConversationAsMarkdown(fs.readFileSync(CLAUDE_FIXTURE, 'utf-8'));
    expect(md).toMatchSnapshot();
    expect(md).not.toContain('**Originator:**');
  });
});

describe('formatConversationAsHTML on Codex rollouts', () => {
  it('renders the Codex markdown through the existing marked path', () => {
    const html = formatConversationAsHTML(fixture);
    expect(html.startsWith('<!DOCTYPE html>')).toBe(true);
    expect(html).toContain('<strong>User</strong>');
    expect(html).toContain('ZEBRA_MARKER_ingest_test');
  });
});
