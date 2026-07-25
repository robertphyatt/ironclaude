import fs from 'fs';
import readline from 'readline';
import crypto from 'crypto';
import { ConversationExchange, ToolCall } from './types.js';

interface CodexRecord { type?: string; timestamp?: string; payload?: any; }

function safeParseArgs(argsStr: unknown): any {
  if (typeof argsStr !== 'string') return argsStr;
  try { return JSON.parse(argsStr); } catch { return argsStr; }
}

function textOf(content: any, blockType: string): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter(b => b && b.type === blockType && typeof b.text === 'string')
      .map(b => b.text).join('\n');
  }
  return '';
}

export async function parseCodexConversation(
  filePath: string, projectName: string, archivePath: string
): Promise<ConversationExchange[]> {
  const exchanges: ConversationExchange[] = [];
  const rl = readline.createInterface({
    input: fs.createReadStream(filePath), crlfDelay: Infinity,
  });

  let sessionId: string | undefined;
  let cwd: string | undefined;
  let cliVersion: string | undefined;
  let lineNumber = 0;
  let current: {
    userMessage: string; userLine: number; assistantMessages: string[];
    lastAssistantLine: number; timestamp: string; toolCalls: ToolCall[];
  } | null = null;

  const finalize = () => {
    if (current && current.assistantMessages.length > 0) {
      const id = crypto.createHash('md5')
        .update(`${archivePath}:${current.userLine}-${current.lastAssistantLine}`)
        .digest('hex');
      const toolCalls = current.toolCalls.map(tc => ({ ...tc, exchangeId: id }));
      exchanges.push({
        id, project: projectName, timestamp: current.timestamp,
        userMessage: current.userMessage,
        assistantMessage: current.assistantMessages.join('\n\n'),
        archivePath, lineStart: current.userLine, lineEnd: current.lastAssistantLine,
        sessionId, cwd, gitBranch: undefined, claudeVersion: cliVersion,
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      });
    }
  };

  for await (const line of rl) {
    lineNumber++;
    let rec: CodexRecord;
    try { rec = JSON.parse(line); } catch { continue; }
    const p = rec.payload;

    if (rec.type === 'session_meta' && p) {
      sessionId = p.session_id ?? sessionId;
      cwd = p.cwd ?? cwd;
      cliVersion = p.cli_version ?? cliVersion;
      continue;
    }
    if (rec.type !== 'response_item' || !p) continue;

    if (p.type === 'message' && p.role === 'user') {
      finalize();
      current = {
        userMessage: textOf(p.content, 'input_text') || '(no text)',
        userLine: lineNumber, assistantMessages: [], lastAssistantLine: lineNumber,
        timestamp: rec.timestamp || new Date().toISOString(), toolCalls: [],
      };
    } else if (p.type === 'message' && p.role === 'assistant' && current) {
      const t = textOf(p.content, 'output_text');
      if (t.trim()) current.assistantMessages.push(t);
      current.lastAssistantLine = lineNumber;
      if (rec.timestamp) current.timestamp = rec.timestamp;
    } else if (p.type === 'function_call' && current) {
      current.toolCalls.push({
        id: crypto.randomUUID(), exchangeId: '',
        toolName: p.name || 'unknown', toolInput: safeParseArgs(p.arguments),
        isError: false, timestamp: rec.timestamp || new Date().toISOString(),
      });
      current.lastAssistantLine = lineNumber;
    }
    // Ignored: event_msg/*, reasoning (encrypted), world_state, turn_context,
    // function_call_output, custom_tool_call* (v1 scope).
  }
  finalize();
  return exchanges;
}
