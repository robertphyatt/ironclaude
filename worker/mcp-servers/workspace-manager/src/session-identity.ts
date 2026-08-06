import type { IronClaudeClient, SessionIdentity } from './types.js';

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

function hasOwn(value: UnknownRecord, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

/**
 * Subagent indicator for the Claude transport, if the request carries one.
 * Returns null for a root turn (and for a transport that says nothing).
 *
 * Each key below is a marker a subagent invocation would set, never a root one.
 * `thread_source` is the same field name Claude Code already sends to hooks —
 * state-activator.sh refuses intent ISSUANCE on `thread_source == "subagent"`,
 * so this closes the matching CONSUMPTION side using the same vocabulary.
 */
function claudeSubagentMarker(meta: UnknownRecord): string | null {
  if (meta.thread_source === 'subagent' || meta.threadSource === 'subagent') {
    const id = meta.agent_id ?? meta.agentId ?? meta.subagent_id ?? meta.subagentId;
    return typeof id === 'string' && id.length > 0 ? id : 'subagent';
  }
  for (const key of ['agent_id', 'agentId', 'subagent_id', 'subagentId'] as const) {
    const value = meta[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return null;
}

export function parseIronClaudeClient(value: unknown): IronClaudeClient {
  if (value === 'claude' || value === 'codex') return value;
  throw new Error(`IRONCLAUDE_CLIENT must be "claude" or "codex", got ${String(value)}`);
}

/**
 * Mirrors state-manager's two trusted identity sources exactly: Claude's PPID
 * file and verified Codex request metadata. Workspace-manager does not accept
 * a third source or fall back between providers.
 */
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
    // PARITY WITH THE CODEX subagent fence below. Codex reports thread_source,
    // so a subagent is refused before it can consume human Git authority. Claude
    // resolves identity from the PPID file, which a subagent SHARES with its
    // root, so the PPID alone cannot separate them.
    //
    // This reads whichever subagent marker the transport does supply and, when
    // present, records a distinct invocation id so requireProviderRoot() refuses
    // exactly as it does for Codex. No marker is asserted to exist today — the
    // repo has no evidence for one — so this cannot be relied on as proof of
    // isolation; it fails closed the moment a marker appears rather than
    // silently continuing to allow subagent consumption.
    const subagentMarker = claudeSubagentMarker(meta);
    const sessionId = text(claudePpidSession, 'Claude PPID session ID');
    return {
      client,
      sessionId,
      invocationThreadId: subagentMarker === null ? null : `${sessionId}:${subagentMarker}`,
      source: 'ppid_file',
    };
  }

  const meta = record(requestMeta, 'Codex request metadata');
  const invocationThreadId = text(meta.threadId, 'Codex threadId');
  const turn = record(meta['x-codex-turn-metadata'], 'x-codex-turn-metadata');
  const sessionId = text(turn.session_id, 'Codex root session_id');
  const nestedThreadId = text(turn.thread_id, 'Codex thread_id');

  if (invocationThreadId !== nestedThreadId) {
    throw new Error('Codex top-level threadId disagrees with nested thread_id');
  }

  const hasParent = hasOwn(turn, 'parent_thread_id');
  const hasFork = hasOwn(turn, 'forked_from_thread_id');
  if (!hasOwn(turn, 'thread_source')) {
    if (hasParent || hasFork) {
      throw new Error('Source-less Codex root metadata cannot contain ancestry fields');
    }
    if (sessionId !== invocationThreadId) {
      throw new Error('Codex root session_id disagrees with root threadId');
    }
  } else if (turn.thread_source === 'user') {
    if (hasParent || hasFork) {
      throw new Error('Codex user root metadata cannot contain ancestry fields');
    }
    if (sessionId !== invocationThreadId) {
      throw new Error('Codex root session_id disagrees with root threadId');
    }
  } else if (turn.thread_source === 'subagent') {
    const parentThreadId = text(turn.parent_thread_id, 'Codex parent_thread_id');
    const forkedFromThreadId = text(turn.forked_from_thread_id, 'Codex forked_from_thread_id');
    if (sessionId === invocationThreadId) {
      throw new Error('Codex subagent thread_id must differ from root session_id');
    }
    if (sessionId !== parentThreadId || sessionId !== forkedFromThreadId) {
      throw new Error('Codex subagent root session fields disagree');
    }
  } else {
    throw new Error('Missing or invalid Codex thread_source');
  }

  return { client, sessionId, invocationThreadId, source: 'codex_meta' };
}
