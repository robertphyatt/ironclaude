# Episodic Memory: Read Codex Transcripts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

Loop: full — size: ~140 lines of renderer in show.ts plus ~120 lines of tests and a snapshot (3 files, ~270 lines); no risk area (no DB writes, no MCP schema, no guards, no concurrency, no git).

**Goal:** Make the episodic-memory `read` tool and `show` CLI render Codex rollouts instead of returning an empty string, with Claude transcripts unchanged.

**Requirements:** `docs/plans/2026-10-09-episodic-codex-read-design.md` (section `## Requirements`, R1–R5)

**Architecture:** `show.ts` gains a Codex branch. `formatConversationAsMarkdown` and `formatConversationAsHTML` first check `isCodexRollout(jsonl)` (the first parseable record has a Codex `type` and a `payload` object). A Codex rollout is rendered by `formatCodexRolloutAsMarkdown(jsonl, startLine, endLine)`, which numbers lines exactly as `codex-parser.ts` does. The HTML mode passes that markdown through the existing `renderMarkdownSafely` (`marked.parse`) path inside a minimal page. `mcp-server.ts` and `show-cli.ts` stay unchanged. Claude rendering code is untouched below the one-line dispatch, and a snapshot taken from the current renderer proves its output stays byte-for-byte identical.

**Tech Stack:** TypeScript (ESM, strict), vitest 3 (snapshots), marked 16.4.2, Node built-in zstd (`readArchiveText`).

## Execution invariants

- Shell state does not persist between steps. Every command uses absolute paths.
- Bash cwd may be `commander/`. Commands `cd` to the package explicitly; git uses `git -C /Users/roberthyatt/Code/ironclaude`.
- zsh `nomatch`: no unquoted globs are used.
- `docs/` is gitignored; plan artifacts are staged with `git add -f`.
- Each verification run writes its own log under `/private/tmp/ironclaude-episodic-codex-read/`.
- Counts in expected results are recorded from the actual run, not predicted.
- vitest writes a new snapshot only when `CI` is unset; the RED run must not set `CI`.

## Verified source facts

- `worker/mcp-servers/episodic-memory/src/show.ts:1` imports `{ marked }` from `marked`; `:26` `formatConversationAsMarkdown(jsonl, startLine?, endLine?)` keeps only `type` `user`/`assistant` records (`:41`), so Codex rollouts return `''`; `:37` `JSON.parse` throws on a malformed line; `:223` `formatConversationAsHTML(jsonl)`; `:787` `renderMarkdownSafely(text)` is a hoisted function declaration wrapping `marked.parse(text, { async: false })`.
- `src/mcp-server.ts:311-316` calls `readArchiveText(resolvedPath)` then `formatConversationAsMarkdown(jsonlContent, params.startLine, params.endLine)`. `src/show-cli.ts:49-55` calls `readArchiveText` then `formatConversationAsHTML(jsonl)` or `formatConversationAsMarkdown(jsonl)`.
- `src/codex-parser.ts:60-63` increments `lineNumber` for every readline line, blank and unparseable lines included (crlfDelay Infinity); `:66-71` reads `session_id`, `cwd`, `cli_version`, `originator` from `session_meta` payloads (last non-null wins); `:75-93` user `input_text`, assistant `output_text`, `function_call` set `lineStart`/`lineEnd`; `function_call_output` and `custom_tool_call*` are ignored by the parser.
- `src/types.ts:18-19` `lineStart: number; lineEnd: number;` on `ConversationExchange`.
- `src/archive-io.ts:70` `readArchiveText(p)` resolves `<p>` or `<p>.zst` and decodes zstd.
- Fixtures: `src/__fixtures__/codex-rollout-sample.jsonl` (7 lines, newline-terminated: session_meta, user, reasoning, assistant, function_call `shell`, two event_msg) and `src/__fixtures__/claude-askuser-session.jsonl` (5 lines; first record `queue-operation` without `payload`; version `2.1.90`, gitBranch `main`).
- Real rollouts (`~/.codex/sessions/2026/10`, 220 files): `session_meta` is line 1 in every file; `custom_tool_call` carries `name` and string `input`; `custom_tool_call_output.output` is an array of `{type:'input_text', text}` blocks; `function_call_output.output` is a string; a `response_item` payload type `agent_message` also exists (skipped, not in R2).
- Tests live beside sources as `src/*.test.ts` (default vitest include); vitest stores snapshots in `src/__snapshots__/<test file>.snap`; `tsconfig.json` includes `src/**/*`; `dist/` is untracked (rebuilt at deploy, outside this plan).

---

## Task 1: Render Codex rollouts in read/show (R1–R5)

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/src/show.ts` (new Codex block before `:26`, dispatch lines at the top of `formatConversationAsMarkdown` and `formatConversationAsHTML`)
- Create: `worker/mcp-servers/episodic-memory/src/show-codex.test.ts`
- Create (written by vitest in Step 2): `worker/mcp-servers/episodic-memory/src/__snapshots__/show-codex.test.ts.snap`

**Step 1: Write the failing tests (RED)**

Create `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/show-codex.test.ts`:

```ts
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
```

What each test catches:
- Test 1 (R1, R2): fails if Codex still returns `''`, if any header field or role/tool rendering is missing, if encrypted reasoning leaks, or if `event_msg` duplicates are rendered (count would be 2).
- Test 2 (R5 `.zst`): fails if the renderer returns `''` (the `toContain` guard) or if the decoded `.zst` text renders differently.
- Test 3 (R3, R2): ranges come from `parseCodexConversation`, so it fails if the renderer's line numbering drifts from the parser's — including dropping the blank line 8, which would shift the second range past its user message — if metadata is read only from the range (line 1 is outside both ranges), if `custom_tool_call` / array output / `function_call_output` are dropped, or if a backtick run in tool text closes the fence early.
- Test 4 (error handling): fails if a malformed line throws or if a malformed first line defeats detection.
- Test 5 (R1 Claude unchanged): the snapshot is recorded in Step 2 from the unchanged renderer, so after Step 3 it fails on any byte of Claude output that differs, including Claude transcripts misrouted to the Codex renderer.
- Test 6 (R4): fails if HTML mode returns `''` or skips marked (no `<strong>`).

**Step 2: Run the tests to verify RED and record the Claude snapshot**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx vitest run src/show-codex.test.ts
```

Expected: 5 failed, 1 passed, and 1 snapshot written. The failures are tests 1, 2, 3, 4 and 6 (empty output from the Claude-only renderer; test 4 throws `SyntaxError` from `JSON.parse` on `garbage first line`). Test 5 passes and writes its snapshot from the unchanged renderer. Record the actual summary; if the counts differ or no snapshot is written, stop and report before editing show.ts.

**Step 3: Confirm the snapshot holds the current Claude rendering**

Run:
```bash
grep -c -e "Plan the KIWI migration" -e "Claude Code Version" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/__snapshots__/show-codex.test.ts.snap
```

Expected: `2`.

**Step 4: Implement the Codex renderer (GREEN)**

Edit `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src/show.ts`. The import on line 1 stays as it is.

Edit 4a — insert this block immediately before `export function formatConversationAsMarkdown(` (after the closing `}` of `interface ConversationMessage`):
```ts
// Codex rollouts (~/.codex/sessions/**/rollout-*.jsonl) hold `{timestamp, type, payload}` records.
const CODEX_RECORD_TYPES = new Set(['session_meta', 'response_item', 'event_msg', 'turn_context']);

interface CodexRecord {
  type?: string;
  timestamp?: string;
  payload?: any;
}

function parseCodexRecord(line: string): CodexRecord | null {
  if (!line.trim()) return null;
  try {
    const rec = JSON.parse(line);
    return rec && typeof rec === 'object' ? rec : null;
  } catch {
    return null;
  }
}

/** True when the first parseable record is a Codex rollout record (a known type with a payload object). */
export function isCodexRollout(jsonl: string): boolean {
  let start = 0;
  while (start < jsonl.length) {
    const newline = jsonl.indexOf('\n', start);
    const end = newline === -1 ? jsonl.length : newline;
    const rec = parseCodexRecord(jsonl.slice(start, end));
    if (rec) {
      return CODEX_RECORD_TYPES.has(String(rec.type)) && !!rec.payload && typeof rec.payload === 'object';
    }
    start = end + 1;
  }
  return false;
}

function codexText(content: unknown, blockType: string): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter(b => b && b.type === blockType && typeof b.text === 'string')
    .map(b => b.text)
    .join('\n');
}

function codexToolOutput(output: unknown): string {
  if (typeof output === 'string') return output;
  if (Array.isArray(output)) {
    return output.map(b => (b && typeof b.text === 'string' ? b.text : JSON.stringify(b))).join('\n');
  }
  return output === undefined ? '' : JSON.stringify(output, null, 2);
}

/** A code fence longer than any backtick run in `text`, so tool text cannot close it early. */
function fencedBlock(text: string, lang = ''): string {
  const longestRun = (text.match(/`+/g) ?? []).reduce((max, run) => Math.max(max, run.length), 0);
  const fence = '`'.repeat(Math.max(3, longestRun + 1));
  return `${fence}${lang}\n${text}\n${fence}\n\n`;
}

function codexToolInput(raw: unknown): string {
  if (typeof raw === 'string') {
    try {
      return fencedBlock(JSON.stringify(JSON.parse(raw), null, 2), 'json');
    } catch {
      return fencedBlock(raw);
    }
  }
  return raw === undefined ? '' : fencedBlock(JSON.stringify(raw, null, 2), 'json');
}

export function formatCodexRolloutAsMarkdown(jsonl: string, startLine?: number, endLine?: number): string {
  // Number lines exactly as codex-parser's readline loop does: every line, 1-indexed, blank ones too.
  const allLines = jsonl.split('\n');
  if (allLines[allLines.length - 1] === '') allLines.pop();

  // Session metadata comes from the whole file, so a paged read still names its session.
  let sessionId: string | undefined;
  let cwd: string | undefined;
  let originator: string | undefined;
  let cliVersion: string | undefined;
  for (const line of allLines) {
    if (!line.includes('"session_meta"')) continue;
    const rec = parseCodexRecord(line);
    if (rec?.type !== 'session_meta' || !rec.payload) continue;
    sessionId = rec.payload.session_id ?? sessionId;
    cwd = rec.payload.cwd ?? cwd;
    originator = rec.payload.originator ?? originator;
    cliVersion = rec.payload.cli_version ?? cliVersion;
  }

  // Apply line range if specified (1-indexed, inclusive)
  const lines = allLines.slice(
    startLine !== undefined ? startLine - 1 : 0,
    endLine !== undefined ? endLine : undefined
  );

  let body = '';
  for (const line of lines) {
    const rec = parseCodexRecord(line);
    if (rec?.type !== 'response_item' || !rec.payload) continue;
    const p = rec.payload;
    const when = rec.timestamp ? ` (${rec.timestamp})` : '';
    if (p.type === 'message' && (p.role === 'user' || p.role === 'assistant')) {
      const text = codexText(p.content, p.role === 'user' ? 'input_text' : 'output_text');
      if (text.trim()) body += `### **${p.role === 'user' ? 'User' : 'Agent'}**${when}\n\n${text}\n\n`;
    } else if (p.type === 'function_call' || p.type === 'custom_tool_call') {
      body += `### **Tool Call:** \`${p.name || 'unknown'}\`${when}\n\n`;
      body += codexToolInput(p.type === 'function_call' ? p.arguments : p.input);
    } else if (p.type === 'function_call_output' || p.type === 'custom_tool_call_output') {
      body += `### **Tool Output**${when}\n\n${fencedBlock(codexToolOutput(p.output))}`;
    }
    // Skipped: encrypted reasoning, agent_message and other payload types; event_msg; turn_context.
  }

  if (!body) return '';

  let output = '# Conversation\n\n## Metadata\n\n';
  if (sessionId) output += `**Session ID:** ${sessionId}\n\n`;
  if (cwd) output += `**Working Directory:** ${cwd}\n\n`;
  if (originator) output += `**Originator:** ${originator}\n\n`;
  if (cliVersion) output += `**Codex CLI Version:** ${cliVersion}\n\n`;
  output += '---\n\n## Messages\n\n';
  return output + body;
}

function formatCodexRolloutAsHTML(jsonl: string): string {
  const markdown = formatCodexRolloutAsMarkdown(jsonl);
  if (!markdown) return '';
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Conversation</title>
</head>
<body>
${renderMarkdownSafely(markdown)}
</body>
</html>`;
}

```

Edit 4b — first statement of `formatConversationAsMarkdown`; replace:
```ts
export function formatConversationAsMarkdown(jsonl: string, startLine?: number, endLine?: number): string {
  const allLines = jsonl.trim().split('\n').filter(line => line.trim());
```
with:
```ts
export function formatConversationAsMarkdown(jsonl: string, startLine?: number, endLine?: number): string {
  if (isCodexRollout(jsonl)) return formatCodexRolloutAsMarkdown(jsonl, startLine, endLine);
  const allLines = jsonl.trim().split('\n').filter(line => line.trim());
```

Edit 4c — first statement of `formatConversationAsHTML`; replace:
```ts
export function formatConversationAsHTML(jsonl: string): string {
  const lines = jsonl.trim().split('\n').filter(line => line.trim());
```
with:
```ts
export function formatConversationAsHTML(jsonl: string): string {
  if (isCodexRollout(jsonl)) return formatCodexRolloutAsHTML(jsonl);
  const lines = jsonl.trim().split('\n').filter(line => line.trim());
```

No other line of show.ts changes.

**Step 5: Run the tests to verify GREEN**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx vitest run src/show-codex.test.ts
```

Expected: 6 passed, 0 failed, and the snapshot matched (none written or updated).

**Step 6: Run the full episodic-memory suite and the type check**

Run:
```bash
mkdir -p /private/tmp/ironclaude-episodic-codex-read
```
Expected: exit 0.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx vitest run > /private/tmp/ironclaude-episodic-codex-read/vitest-full.log 2>&1; echo "exit=$?"
```
Expected: `exit=0`; record the `Test Files` and `Tests` summary lines from the log.

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory && npx tsc --noEmit > /private/tmp/ironclaude-episodic-codex-read/tsc.log 2>&1; echo "exit=$?"
```
Expected: `exit=0` with an empty log. Any error in `src/show.ts` or `src/show-codex.test.ts` is fixed before staging; any other error is recorded and reported as pre-existing with its file and line.

**Step 7: Stage changes**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/episodic-memory/src/show.ts worker/mcp-servers/episodic-memory/src/show-codex.test.ts worker/mcp-servers/episodic-memory/src/__snapshots__/show-codex.test.ts.snap
```

Expected: exit 0 (professional mode blocks commit; the operator commits after the final review).
