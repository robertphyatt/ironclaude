import { describe, expect, it } from 'vitest';
import { parseIronClaudeClient, resolveSessionIdentity } from '../session-identity.js';

const ROOT = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const CHILD = '019f7cdf-023c-74e0-9ead-9c155636885d';

const rootMeta = {
  threadId: ROOT,
  'x-codex-turn-metadata': {
    session_id: ROOT,
    thread_id: ROOT,
    thread_source: 'user',
    turn_id: 'turn-root',
  },
};

const subagentMeta = {
  threadId: CHILD,
  'x-codex-turn-metadata': {
    session_id: ROOT,
    thread_id: CHILD,
    thread_source: 'subagent',
    parent_thread_id: ROOT,
    forked_from_thread_id: ROOT,
    turn_id: 'turn-child',
  },
};

describe('resolveSessionIdentity', () => {
  it('uses only the Claude PPID session for Claude', () => {
    expect(resolveSessionIdentity('claude', undefined, 'claude-root')).toEqual({
      client: 'claude', sessionId: 'claude-root', invocationThreadId: null, source: 'ppid_file',
    });
  });

  it('uses Codex root session metadata for root and subagent calls', () => {
    expect(resolveSessionIdentity('codex', rootMeta).sessionId).toBe(ROOT);
    const child = resolveSessionIdentity('codex', subagentMeta);
    expect(child.sessionId).toBe(ROOT);
    expect(child.invocationThreadId).toBe(CHILD);
  });

  it.each([
    ['missing metadata', undefined],
    ['missing root session', { threadId: ROOT, 'x-codex-turn-metadata': { thread_id: ROOT, thread_source: 'user' } }],
    ['root mismatch', { threadId: CHILD, 'x-codex-turn-metadata': { session_id: ROOT, thread_id: CHILD, thread_source: 'user' } }],
    ['child mismatch', { threadId: ROOT, 'x-codex-turn-metadata': { session_id: ROOT, thread_id: CHILD, thread_source: 'subagent', parent_thread_id: ROOT, forked_from_thread_id: ROOT } }],
    ['parent mismatch', { threadId: CHILD, 'x-codex-turn-metadata': { session_id: ROOT, thread_id: CHILD, thread_source: 'subagent', parent_thread_id: 'other', forked_from_thread_id: ROOT } }],
    ['fork mismatch', { threadId: CHILD, 'x-codex-turn-metadata': { session_id: ROOT, thread_id: CHILD, thread_source: 'subagent', parent_thread_id: ROOT, forked_from_thread_id: 'other' } }],
  ])('rejects invalid Codex identity: %s', (_label, meta) => {
    expect(() => resolveSessionIdentity('codex', meta, 'claude-fallback')).toThrow();
  });

  it('does not accept turn_id as Codex session identity', () => {
    expect(() => resolveSessionIdentity('codex', {
      threadId: ROOT,
      'x-codex-turn-metadata': { turn_id: ROOT, thread_id: ROOT, thread_source: 'user' },
    })).toThrow();
  });

  it('rejects missing Claude PPID session and Codex metadata fallback', () => {
    expect(() => resolveSessionIdentity('claude', rootMeta)).toThrow();
  });

  it('rejects an unknown installation client', () => {
    expect(() => parseIronClaudeClient('other')).toThrow('IRONCLAUDE_CLIENT');
  });
});
