import { describe, expect, it } from 'vitest';
import { getSession, initDb, upsertSession } from '../db.js';
import { resolveSessionIdentity, type SessionIdentity } from '../session-identity.js';
import { dispatchTool } from '../tool-dispatch.js';

const ROOT_A = 'root-a';
const ROOT_B = 'root-b';
const CHILD_A = 'child-a';

function payload(result: { content: Array<{ text: string }> }): Record<string, unknown> {
  return JSON.parse(result.content[0].text) as Record<string, unknown>;
}

function identity(sessionId: string): SessionIdentity {
  return { client: 'codex', sessionId, invocationThreadId: sessionId, source: 'codex_meta' };
}

describe('dispatchTool', () => {
  it('isolates interleaved reads and writes by explicit root session', () => {
    const db = initDb(':memory:');
    upsertSession(db, {
      terminal_session: ROOT_A,
      professional_mode: 'on',
      testing_theatre_checked: 0,
    });
    upsertSession(db, {
      terminal_session: ROOT_B,
      professional_mode: 'off',
      testing_theatre_checked: 1,
    });

    expect(payload(dispatchTool('get_professional_mode', {}, db, identity(ROOT_A))).professional_mode).toBe('on');
    expect(payload(dispatchTool('get_professional_mode', {}, db, identity(ROOT_B))).professional_mode).toBe('off');
    expect(payload(dispatchTool('get_testing_theatre_status', {}, db, identity(ROOT_A))).testing_theatre_checked).toBe(0);
    expect(payload(dispatchTool('get_testing_theatre_status', {}, db, identity(ROOT_B))).testing_theatre_checked).toBe(1);

    const beforeB = getSession(db, ROOT_B);
    dispatchTool('set_testing_theatre_checked', {}, db, identity(ROOT_A));

    expect(getSession(db, ROOT_A)?.testing_theatre_checked).toBe(1);
    expect(getSession(db, ROOT_B)).toEqual(beforeB);
  });

  it('rejects a missing explicit session before routing', () => {
    const db = initDb(':memory:');
    expect(() => dispatchTool('get_professional_mode', {}, db, identity(''))).toThrow(
      'Resolved session ID is required',
    );
  });

  it('returns the existing not-found response for the exact resolved session', () => {
    const db = initDb(':memory:');
    const result = payload(dispatchTool('get_plan_status', {}, db, identity('missing-root')));
    expect(result).toEqual({ error: 'Session not found', session_id: 'missing-root' });
  });

  it('routes root and subagent Codex calls to the same root row', () => {
    const db = initDb(':memory:');
    upsertSession(db, { terminal_session: ROOT_A, professional_mode: 'on' });

    const root = resolveSessionIdentity('codex', {
      threadId: ROOT_A,
      'x-codex-turn-metadata': {
        session_id: ROOT_A,
        thread_id: ROOT_A,
        thread_source: 'user',
      },
    });
    const child = resolveSessionIdentity('codex', {
      threadId: CHILD_A,
      'x-codex-turn-metadata': {
        session_id: ROOT_A,
        thread_id: CHILD_A,
        thread_source: 'subagent',
        parent_thread_id: ROOT_A,
        forked_from_thread_id: ROOT_A,
      },
    });

    expect(payload(dispatchTool('get_plan_status', {}, db, root)).session_id).toBe(ROOT_A);
    expect(payload(dispatchTool('get_plan_status', {}, db, child)).session_id).toBe(ROOT_A);
  });
});
