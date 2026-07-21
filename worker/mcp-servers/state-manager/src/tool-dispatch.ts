import type Database from 'better-sqlite3';
import type { SessionIdentity } from './session-identity.js';
import type { RuntimeFingerprintCapture } from './runtime-fingerprint.js';
import { handleReadTool, readToolNames } from './tools/read-tools.js';
import { handleWriteTool, writeToolNames } from './tools/write-tools.js';

export type ToolResult = {
  content: Array<{ type: string; text: string }>;
  isError?: boolean;
};

export function dispatchTool(
  name: string,
  args: Record<string, unknown>,
  db: Database.Database,
  identity: SessionIdentity,
  runtimeFingerprint?: RuntimeFingerprintCapture,
): ToolResult {
  if (!identity.sessionId) {
    throw new Error('Resolved session ID is required before tool dispatch');
  }

  if (readToolNames.has(name)) {
    return handleReadTool(name, args, db, identity.sessionId, identity, runtimeFingerprint);
  }

  if (writeToolNames.has(name)) {
    return handleWriteTool(name, args, db, identity.sessionId);
  }

  throw new Error(`Unknown tool: ${name}`);
}
