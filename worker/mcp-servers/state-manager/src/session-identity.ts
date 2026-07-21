export type IronClaudeClient = 'claude' | 'codex';

export interface SessionIdentity {
  client: IronClaudeClient;
  sessionId: string;
  invocationThreadId: string | null;
  source: 'ppid_file' | 'codex_meta';
}

type UnknownRecord = Record<string, unknown>;

function record(value: unknown, label: string): UnknownRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`Missing or invalid ${label}`);
  }
  return value as UnknownRecord;
}

function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Missing or invalid ${label}`);
  }
  return value;
}

export function parseIronClaudeClient(value: unknown): IronClaudeClient {
  if (value === 'claude' || value === 'codex') return value;
  throw new Error(`IRONCLAUDE_CLIENT must be "claude" or "codex", got ${String(value)}`);
}

export function resolveSessionIdentity(
  client: IronClaudeClient,
  requestMeta?: unknown,
  claudePpidSession?: string | null,
): SessionIdentity {
  if (client === 'claude') {
    const meta = requestMeta && typeof requestMeta === 'object' ? requestMeta as UnknownRecord : {};
    if ('threadId' in meta || 'x-codex-turn-metadata' in meta) {
      throw new Error('Codex request metadata cannot identify a Claude session');
    }
    return {
      client,
      sessionId: text(claudePpidSession, 'Claude PPID session ID'),
      invocationThreadId: null,
      source: 'ppid_file',
    };
  }

  const meta = record(requestMeta, 'Codex request metadata');
  const invocationThreadId = text(meta.threadId, 'Codex threadId');
  const turn = record(meta['x-codex-turn-metadata'], 'x-codex-turn-metadata');
  const sessionId = text(turn.session_id, 'Codex root session_id');
  const nestedThreadId = text(turn.thread_id, 'Codex thread_id');
  const threadSource = text(turn.thread_source, 'Codex thread_source');

  if (invocationThreadId !== nestedThreadId) {
    throw new Error('Codex top-level threadId disagrees with nested thread_id');
  }

  if (threadSource === 'subagent') {
    const parentThreadId = text(turn.parent_thread_id, 'Codex parent_thread_id');
    const forkedFromThreadId = text(turn.forked_from_thread_id, 'Codex forked_from_thread_id');
    if (sessionId !== parentThreadId || sessionId !== forkedFromThreadId) {
      throw new Error('Codex subagent root session fields disagree');
    }
  } else if (sessionId !== invocationThreadId) {
    throw new Error('Codex root session_id disagrees with root threadId');
  }

  return { client, sessionId, invocationThreadId, source: 'codex_meta' };
}
