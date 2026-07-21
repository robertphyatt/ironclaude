#!/usr/bin/env node
/**
 * MCP Server for State Management.
 *
 * Provides tools to manage persistent state (plans, tasks, key-value data)
 * for Claude Code plugin sessions via the Model Context Protocol.
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

import path from 'path';
import fs from 'fs';
import os from 'os';

import { initDb } from './db.js';
import { parseIronClaudeClient, resolveSessionIdentity } from './session-identity.js';
import { dispatchTool } from './tool-dispatch.js';
import { captureRuntimeFingerprint } from './runtime-fingerprint.js';

// Error sideband: write MCP tool errors to a file that hooks can surface
const ERROR_LOG_PATH = path.join(os.homedir(), '.claude', 'ironclaude-errors.log');

function appendErrorLog(tool: string, sessionId: string, error: string): void {
  try {
    const ts = new Date().toISOString();
    const line = `[${ts}] session=${sessionId} tool=${tool} error="${error.replace(/"/g, '\\"')}"\n`;
    fs.appendFileSync(ERROR_LOG_PATH, line);
  } catch {
    // Best-effort — if we can't write the error log, we still return the error to Claude
  }
}

// Claude PPID transport state. Codex never reads this path.
let _ppidFilePath: string | null = null;
let _initialBindComplete = false;
const client = parseIronClaudeClient(process.env.IRONCLAUDE_CLIENT);
const runtimeFingerprint = captureRuntimeFingerprint(import.meta.url, client);

/**
 * Read session ID from PPID file.
 * Returns null if file doesn't exist or contains unresolved placeholder.
 */
function readSessionFromPpidFile(filePath: string): string | null {
  try {
    const sid = fs.readFileSync(filePath, 'utf-8').trim();
    if (sid && !sid.startsWith('${')) {
      return sid;
    }
  } catch {
    // File doesn't exist yet
  }
  return null;
}

import { readToolDefinitions } from './tools/read-tools.js';
import { writeToolDefinitions } from './tools/write-tools.js';

async function readClaudeSessionForCall(): Promise<string | null> {
  if (!_ppidFilePath) return null;

  if (_initialBindComplete) {
    return readSessionFromPpidFile(_ppidFilePath);
  }

  const maxAttempts = 5;
  const delayMs = 300;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const sessionId = readSessionFromPpidFile(_ppidFilePath);
    if (sessionId) {
      _initialBindComplete = true;
      console.error(`Resolved Claude session via PPID file (attempt ${attempt}/${maxAttempts})`);
      return sessionId;
    }
    if (attempt < maxAttempts) {
      await new Promise(resolve => setTimeout(resolve, delayMs));
    }
  }

  return null;
}

// Error Handling Utility

function handleError(error: unknown): string {
  if (error instanceof Error) {
    return `Error: ${error.message}`;
  }
  return `Error: ${String(error)}`;
}

// Create MCP Server

const server = new Server(
  {
    name: 'state-manager',
    version: runtimeFingerprint.ok ? runtimeFingerprint.runtime.plugin_version : 'unknown',
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

// Register Tools

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      ...readToolDefinitions,
      ...writeToolDefinitions,
    ],
  };
});

// Handle Tool Calls

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  let resolvedSessionId = 'UNRESOLVED';
  try {
    const { name, arguments: args } = request.params;
    const claudeSession = client === 'claude' ? await readClaudeSessionForCall() : null;
    const identity = resolveSessionIdentity(client, request.params._meta, claudeSession);
    resolvedSessionId = identity.sessionId;
    const db = initDb();
    return dispatchTool(name, (args ?? {}) as Record<string, unknown>, db, identity, runtimeFingerprint);
  } catch (error) {
    const errorMsg = handleError(error);
    const toolName = request.params?.name ?? 'unknown';
    appendErrorLog(toolName, resolvedSessionId, errorMsg);
    return {
      content: [{ type: 'text', text: errorMsg }],
      isError: true,
    };
  }
});

// Main Function

async function main() {
  // Initialize database
  const db = initDb();
  console.error('Database initialized');

  // Claude uses its PPID file transport. Codex resolves native metadata per call.
  if (client === 'claude') {
    const claudePpid = process.env.CLAUDE_PPID;
    if (claudePpid) {
      _ppidFilePath = path.join(os.homedir(), '.claude', `ironclaude-session-${claudePpid}.id`);
      console.error(`Will resolve Claude session via PPID file on each tool call: ${_ppidFilePath}`);
    } else {
      console.error('CRITICAL: CLAUDE_PPID not set. Claude session resolution will fail.');
    }
  } else {
    console.error('Will resolve Codex root session from native MCP metadata on each tool call');
  }

  console.error('State Manager MCP server running via stdio');

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

// Run the Server

main().catch((error) => {
  console.error('Server error:', error);
  process.exit(1);
});
