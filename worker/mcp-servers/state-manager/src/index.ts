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

import { fileURLToPath } from 'url';
import path from 'path';
import fs from 'fs';
import os from 'os';

import { initDb } from './db.js';

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

// Session binding state
let _ppidFilePath: string | null = null;
let _sessionBound = false;
let _sessionBindingSource: 'env' | 'ppid_file' | 'none' = 'none';

import { readToolDefinitions, readToolNames, handleReadTool, setCurrentSession as setReadSession, setSessionBindingSource } from './tools/read-tools.js';
import { writeToolDefinitions, writeToolNames, handleWriteTool, setCurrentSession as setWriteSession } from './tools/write-tools.js';

// Read plugin version from plugin.json (single source of truth)
function readPluginVersion(): string {
  try {
    const __filename = fileURLToPath(import.meta.url);
    const __dirname = path.dirname(__filename);
    const pluginJsonPath = path.resolve(__dirname, '..', '..', '..', '.claude-plugin', 'plugin.json');
    const pluginJson = JSON.parse(fs.readFileSync(pluginJsonPath, 'utf-8'));
    return pluginJson.version || 'unknown';
  } catch {
    console.error('Warning: Could not read plugin.json for version');
    return 'unknown';
  }
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
    version: readPluginVersion(),
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
  try {
    // Lazy session binding from PPID file with retry (runs once on first tool call)
    if (!_sessionBound && _ppidFilePath) {
      const maxAttempts = 5;
      const delayMs = 300;
      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
          const sid = fs.readFileSync(_ppidFilePath, 'utf-8').trim();
          if (sid && !sid.startsWith('${')) {
            setReadSession(sid);
            setWriteSession(sid);
            _sessionBound = true;
            _sessionBindingSource = 'ppid_file';
            setSessionBindingSource('ppid_file');
            console.error(`Bound to session via PPID file: ${sid} (attempt ${attempt}/${maxAttempts})`);
            break;
          }
        } catch {
          // File doesn't exist yet
        }
        if (attempt < maxAttempts) {
          await new Promise(resolve => setTimeout(resolve, delayMs));
        }
      }
    }

    const { name, arguments: args } = request.params;
    const db = initDb();

    // Read-only tools
    if (readToolNames.has(name)) {
      return handleReadTool(name, (args ?? {}) as Record<string, unknown>, db, '');
    }

    // Write tools
    if (writeToolNames.has(name)) {
      return handleWriteTool(name, (args ?? {}) as Record<string, unknown>, db, '');
    }

    throw new Error(`Unknown tool: ${name}`);
  } catch (error) {
    const errorMsg = handleError(error);
    const toolName = request.params?.name ?? 'unknown';
    const sessionTag = _ppidFilePath ?? 'UNKNOWN';
    appendErrorLog(toolName, sessionTag, errorMsg);
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

  // Primary: eager session binding from env var (set via .mcp.json env block)
  const envSessionId = process.env.CLAUDE_SESSION_ID;
  if (envSessionId && !envSessionId.startsWith('${')) {
    setReadSession(envSessionId);
    setWriteSession(envSessionId);
    _sessionBound = true;
    _sessionBindingSource = 'env';
    setSessionBindingSource('env');
    console.error(`Bound to session via env var: ${envSessionId}`);
  } else {
    console.error(`CLAUDE_SESSION_ID not set or unsubstituted (${process.env.CLAUDE_SESSION_ID ?? 'unset'}), will use PPID file fallback`);
  }

  // Fallback: PPID file binding (lazy with retry on first tool call)
  const claudePpid = process.env.CLAUDE_PPID;
  if (claudePpid) {
    _ppidFilePath = path.join(os.homedir(), '.claude', `ironclaude-session-${claudePpid}.id`);
    if (!_sessionBound) {
      console.error(`Will bind to session via PPID file on first tool call: ${_ppidFilePath}`);
    }
  } else {
    console.error(`CRITICAL: CLAUDE_PPID not set. PPID fallback will fail.`);
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
